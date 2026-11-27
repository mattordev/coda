import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch

from runtime.schedule_approval import propose_schedule, save_confirmed_schedule
from runtime.schedule_store import ScheduleStore
from runtime.schedule_worker import (
    SchedulerAlreadyRunning, SchedulerService, run_due_schedule, run_scheduler, scheduler_ownership,
)
from runtime.system_policy import SystemPolicyError


class ScheduleApprovalTests(unittest.TestCase):
    def test_service_reports_corrupt_storage_without_dispatching(self):
        corrupt = self.policy.with_name("corrupt.sqlite")
        corrupt.write_bytes(b"not a scheduler database")
        notified = Event()
        notices = []

        def notify(notice):
            notices.append(notice)
            notified.set()

        executor = Mock()
        service = SchedulerService(corrupt, self.policy, executor, notify=notify)
        self.addCleanup(service.close)
        service.start()
        self.assertTrue(notified.wait(5))
        self.assertTrue(service.close())
        self.assertEqual(notices, [{"service_error": "Schedule storage is unavailable."}])
        self.assertEqual(corrupt.read_bytes(), b"not a scheduler database")
        executor.assert_not_called()

    @unittest.skipUnless(sys.platform in ("win32", "linux"), "Windows and Linux process ownership")
    def test_background_requires_its_own_grant_and_uses_an_opaque_job_id(self):
        from runtime.background_actions import BackgroundActions
        from utils.on_command import _on_command, cancel_pending_confirmation

        self.addCleanup(cancel_pending_confirmation)
        jobs = BackgroundActions()
        self.addCleanup(jobs.close, 5)
        with (
            patch.dict(os.environ, {"CODA_SYSTEM_POLICY": str(self.policy)}),
            patch("runtime.action_commands.background_actions", jobs),
        ):
            message = "run action probe in background"
            self.assertIn("unavailable", _on_command(message, {}).response_text)
            self.action["background"] = True
            self.write_policy()
            self.assertIn("Say confirm", _on_command(message, {}).response_text)
            queued = _on_command("confirm", {}).response_text
            job_id = queued.split("Job ID: ")[1].rstrip(".")
            self.assertRegex(job_id, r"^[0-9a-f]{32}$")
            status = "action status " + job_id
            deadline = monotonic() + 5
            while "still running" in (response := _on_command(status, {}).response_text):
                self.assertLess(monotonic(), deadline)
                sleep(0.05)
            self.assertIn("Exit code: 0", response)

    @unittest.skipUnless(sys.platform in ("win32", "linux"), "Windows and Linux process ownership")
    def test_inspection_reads_only_live_owned_jobs(self):
        from runtime.background_actions import BackgroundActions
        from utils.on_command import _on_command, cancel_pending_confirmation

        jobs = BackgroundActions()
        self.addCleanup(jobs.close, 5)
        self.addCleanup(cancel_pending_confirmation)
        self.action.update(background=True, argv=["-I", "-c", "import time; time.sleep(20)"])
        self.write_policy()
        with (
            patch.dict(os.environ, {"CODA_SYSTEM_POLICY": str(self.policy)}),
            patch("runtime.action_commands.background_actions", jobs),
        ):
            def call(message):
                return _on_command(message, {}).response_text

            self.assertIn("Supply the background job ID", call(f"action inspect {os.getpid()}"))
            self.assertIn("No live resource", call("action inspect " + "0" * 32))
            self.assertIn("Say confirm", call("run action probe in background"))
            job_id = call("confirm").split("Job ID: ")[1].rstrip(".")
            inspect = "action inspect " + job_id
            deadline = monotonic() + 8
            while "No live resource" in (response := call(inspect)):
                self.assertLess(monotonic(), deadline)
                sleep(0.05)
            self.assertIn("CPU seconds", response)
            self.assertIn("MiB of resident memory", response)
            self.assertIn("Cancellation requested", call("action cancel " + job_id))
            self.assertTrue(jobs.close(5))
            self.assertIn("cancelled", call("action status " + job_id))
            self.assertIn("No live resource", call(inspect))

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        self.store = ScheduleStore(root / "schedules.sqlite")
        self.policy = root / "policy.json"
        self.action = {"executable": str(Path(sys.executable).resolve()),
                       "cwd": str(root), "argv": ["--version"], "scheduled": True}
        self.write_policy()
        self.enterContext(patch("runtime.schedule_approval.time", return_value=0))

    def write_policy(self):
        self.policy.write_text(json.dumps({"enabled": True, "actions": {"probe": self.action}}),
                               encoding="utf-8")

    def propose(self, **kwargs):
        return propose_schedule(self.store, self.policy, action="probe",
                                local_time="2027-01-15T09:00", timezone="UTC", **kwargs)

    def test_policy_change_between_proposal_and_confirmation_prevents_save(self):
        proposal = self.propose()
        self.action["argv"] = ["-V"]
        self.write_policy()
        with self.assertRaisesRegex(SystemPolicyError, "changed"):
            save_confirmed_schedule(self.store, self.policy, proposal)
        self.assertEqual(self.store.list_tasks(), [])

    def test_interactive_confirmation_is_required_and_consumed(self):
        from utils.on_command import _on_command, cancel_pending_confirmation

        self.addCleanup(cancel_pending_confirmation)
        message = 'schedule create {"action":"probe","local_time":"2027-01-15T09:00","timezone":"UTC"}'
        with patch.dict(os.environ, {"CODA_SYSTEM_POLICY": str(self.policy)}):
            preview = _on_command(message, {})
            store = ScheduleStore(self.policy.with_suffix(".schedules.sqlite"))
            self.assertIn("Say confirm or cancel", preview.response_text)
            self.assertTrue(preview.open_follow_up)
            self.assertEqual(store.list_tasks(), [])
            self.assertIn("cancelled", _on_command("cancel", {}).response_text)
            self.assertEqual(store.list_tasks(), [])
            _on_command(message, {})
            self.assertIn("Schedule saved", _on_command("confirm", {}).response_text)
            self.assertFalse(cancel_pending_confirmation())
            self.assertEqual(len(store.list_tasks()), 1)

    def test_ambiguous_local_time_prompts_for_offset_without_pending_confirmation(self):
        from utils.on_command import _on_command, cancel_pending_confirmation

        self.addCleanup(cancel_pending_confirmation)
        message = 'schedule create {"action":"probe","local_time":"2026-10-25T01:30","timezone":"Europe/London"}'
        with patch.dict(os.environ, {"CODA_SYSTEM_POLICY": str(self.policy)}):
            self.assertIn("Specify its UTC offset", _on_command(message, {}).response_text)
            self.assertFalse(cancel_pending_confirmation())

    @unittest.skipUnless(sys.platform in ("win32", "linux"), "Windows and Linux process ownership")
    def test_interactive_action_revalidates_then_returns_real_process_output(self):
        from utils.on_command import _on_command, cancel_pending_confirmation

        self.addCleanup(cancel_pending_confirmation)
        with patch.dict(os.environ, {"CODA_SYSTEM_POLICY": str(self.policy)}):
            self.assertIn("without additional arguments",
                          _on_command("run action probe --help", {}).response_text)
            preview = _on_command("Run action probe.", {})
            self.assertIn("Say confirm", preview.response_text)
            self.assertTrue(preview.open_follow_up)
            self.action["argv"] = ["-V"]
            self.write_policy()
            self.assertIn("policy changed", _on_command("confirm", {}).response_text)
            _on_command("run action probe", {})
            response = _on_command("confirm", {}).response_text
            self.assertIn("Exit code: 0", response)
            self.assertIn("Python ", response)
            self.assertFalse(cancel_pending_confirmation())

    def test_confirmed_edit_cannot_overwrite_a_newer_definition(self):
        task = save_confirmed_schedule(self.store, self.policy, self.propose())
        stale = self.propose(task_id=task, interval=30)
        fresh = self.propose(task_id=task, interval=60)
        self.assertEqual(save_confirmed_schedule(self.store, self.policy, fresh), task)
        with self.assertRaisesRegex(ValueError, "changed"):
            save_confirmed_schedule(self.store, self.policy, stale)
        self.assertEqual(self.store.list_tasks()[0]["interval"], 60)

    def test_revoked_grant_denies_a_due_occurrence_before_execution(self):
        proposal = self.propose(interval=30)
        save_confirmed_schedule(self.store, self.policy, proposal)
        self.action["scheduled"] = False
        self.write_policy()
        executor = Mock()
        result = run_due_schedule(self.store, self.policy, executor, now=proposal.due, grace=0)
        self.assertEqual(result["state"], "denied")
        self.assertEqual(self.store.history()[0]["state"], "denied")
        self.assertEqual(self.store.list_tasks()[0]["status"], "paused")
        self.assertIsNone(run_due_schedule(self.store, self.policy, executor, now=proposal.due + 30, grace=0))
        executor.assert_not_called()

    def test_uncertain_executor_error_does_not_replay_the_occurrence(self):
        proposal = self.propose()
        save_confirmed_schedule(self.store, self.policy, proposal)
        executor = Mock(side_effect=RuntimeError("unknown external effect"))
        result = run_due_schedule(self.store, self.policy, executor, now=proposal.due, grace=0)
        self.assertEqual(result["state"], "interrupted")
        self.assertIsNone(run_due_schedule(self.store, self.policy, executor, now=proposal.due, grace=0))
        executor.assert_called_once()
        self.assertEqual(executor.call_args.args[0].argv, ("--version",))

    def test_second_worker_cannot_recover_a_live_workers_claim(self):
        proposal = self.propose()
        save_confirmed_schedule(self.store, self.policy, proposal)
        self.store.claim(proposal.due, grace=0)
        stop = Event()
        executor = Mock()
        with scheduler_ownership(self.store.path):
            with self.assertRaises(SchedulerAlreadyRunning):
                run_scheduler(self.store, self.policy, executor, stop)
            self.assertEqual(self.store.history()[0]["state"], "claimed")
        with patch("runtime.schedule_worker.run_due_schedule", side_effect=lambda *a, **kw: stop.set()):
            run_scheduler(self.store, self.policy, executor, stop)
        self.assertEqual(self.store.history()[0]["state"], "interrupted")
        executor.assert_not_called()

    def test_service_shutdown_signals_the_active_executor_and_releases_ownership(self):
        proposal = self.propose()
        save_confirmed_schedule(self.store, self.policy, proposal)
        started = Event()

        def execute(_action, _secrets, stop):
            started.set()
            return "cancelled" if stop.wait(5) else "failed"

        service = SchedulerService(self.store.path, self.policy, execute)
        self.addCleanup(service.close)
        with patch("runtime.schedule_worker.time", return_value=proposal.due):
            service.start()
            self.assertTrue(started.wait(5))
            self.assertTrue(service.close())
        with scheduler_ownership(self.store.path):
            self.assertIsNone(service.failure)
            self.assertEqual(self.store.history()[0]["state"], "cancelled")

    @unittest.skipUnless(sys.platform in ("win32", "linux"), "Windows and Linux process ownership")
    def test_service_runs_a_saved_approved_program_and_persists_completion(self):
        proposal = self.propose()
        task = save_confirmed_schedule(self.store, self.policy, proposal)
        service = SchedulerService(self.store.path, self.policy)
        self.addCleanup(service.close)
        with patch("runtime.schedule_worker.time", return_value=proposal.due):
            service.start()
            deadline = monotonic() + 5
            while not self.store.pending_attention() and monotonic() < deadline:
                sleep(0.05)
            self.assertTrue(service.close())
        self.assertIsNone(service.failure)
        self.assertEqual(self.store.history(), [
            {"task_id": task, "due": proposal.due, "state": "completed"},
        ])
