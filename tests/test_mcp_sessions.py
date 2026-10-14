"""Real-process proofs of retained state, recovery and lifecycle ownership."""

from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
import sys
import tempfile
import time
import unittest

import psutil

from ai.intents import IntentRequest
from ai.mcp.config import LocalServerDefinition
from ai.mcp.runtime import LocalMCPManager


class PersistentSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pid = Path(self.temp.name) / "pid"
        self.started = Path(self.temp.name) / "started"
        self.definition = LocalServerDefinition(
            "test", sys.executable,
            args=(str(Path(__file__).with_name("mcp_stdio_server.py")), "normal", str(self.pid), str(self.started)),
            trusted=True, allowed_tools=frozenset({"increment", "slow", "disconnect_call"}),
            session_mode="persistent", startup_timeout_seconds=15, timeout_seconds=3,
        )

    def manager(self, **changes):
        manager = LocalMCPManager([replace(self.definition, **changes)])
        self.addCleanup(manager.close)
        return manager

    def invoke(self, manager, name="increment", arguments=None, **options):
        executor = manager.executor(f"mcp.test.{name}")
        return executor.execute(IntentRequest(
            executor._intent, name, 1.0, arguments=arguments or {},
        ), **options)

    def stopped(self, pid):
        deadline = time.monotonic() + 5
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(psutil.pid_exists(pid))

    def test_persistent_calls_share_process_and_state_until_explicit_retirement(self):
        manager = self.manager()
        self.assertTrue(manager.discover("test").success)
        first = self.invoke(manager).data["structuredContent"]
        second = self.invoke(manager).data["structuredContent"]
        self.assertEqual(first["pid"], second["pid"])
        self.assertEqual((first["count"], second["count"]), (1, 2))
        self.assertTrue(manager.retire("test").success)
        self.stopped(first["pid"])
        fresh = self.invoke(manager).data["structuredContent"]
        self.assertEqual(fresh["count"], 1)
        self.assertNotEqual(fresh["pid"], first["pid"])

    def test_operation_mode_does_not_preserve_state(self):
        manager = self.manager(session_mode="operation")
        self.assertTrue(manager.discover("test").success)
        first = self.invoke(manager).data["structuredContent"]
        second = self.invoke(manager).data["structuredContent"]
        self.assertEqual((first["count"], second["count"]), (1, 1))
        self.stopped(first["pid"])
        self.stopped(second["pid"])

    def test_concurrent_first_use_creates_one_session(self):
        manager = self.manager()
        results = []
        workers = [Thread(target=lambda: results.append(manager.discover("test"))) for _ in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(20)
            self.assertFalse(worker.is_alive())
        self.assertTrue(all(result.success for result in results))
        first = self.invoke(manager).data["structuredContent"]
        self.assertEqual(first["pid"], int(self.pid.read_text()))
        self.assertEqual(self.invoke(manager).data["structuredContent"]["count"], 2)

    def test_idle_policy_closes_process_and_later_call_reconnects(self):
        manager = self.manager(idle_timeout_seconds=0.2)
        self.assertTrue(manager.discover("test").success)
        first = self.invoke(manager).data["structuredContent"]
        self.stopped(first["pid"])
        self.assertEqual(self.invoke(manager).data["structuredContent"]["count"], 1)

    def test_indefinite_retention_and_explicit_eager_start(self):
        manager = self.manager(startup_mode="eager")
        self.assertFalse(self.pid.exists())
        self.assertTrue(manager.start_eager()["test"].success)
        first = self.invoke(manager).data["structuredContent"]
        time.sleep(0.25)
        self.assertEqual(self.invoke(manager).data["structuredContent"]["pid"], first["pid"])
        self.assertTrue(manager.close()["test"].success)
        self.stopped(first["pid"])
        self.assertEqual(manager.discover("test").error.code, "cancelled")

    def test_disconnect_is_not_replayed_and_next_request_recovers(self):
        manager = self.manager()
        self.assertTrue(manager.discover("test").success)
        pid = int(self.pid.read_text())
        failed = self.invoke(manager, "disconnect_call")
        self.assertFalse(failed.success)
        self.stopped(pid)
        self.assertEqual(self.invoke(manager).data["structuredContent"]["count"], 1)

    def test_timeout_and_cancellation_retire_active_session(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                manager = self.manager(timeout_seconds=0.4)
                self.assertTrue(manager.discover("test").success)
                pid = int(self.pid.read_text())
                event = Event()
                results = []
                self.started.unlink(missing_ok=True)
                worker = Thread(target=lambda: results.append(
                    self.invoke(manager, "slow", {"delay": 60}, cancel_event=event)
                ))
                worker.start()
                self.addCleanup(lambda: (event.set(), worker.join(15)))
                if cancel:
                    deadline = time.monotonic() + 3
                    while not self.started.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(self.started.exists())
                    event.set()
                worker.join(15)
                self.assertFalse(worker.is_alive())
                self.assertEqual(results[0].error.code, "cancelled" if cancel else "timeout")
                self.stopped(pid)
                self.assertTrue(self.invoke(manager).success)

    def test_configuration_replacement_cleans_process_and_invalidates_executor(self):
        manager = self.manager()
        self.assertTrue(manager.discover("test").success)
        old = manager.executor("mcp.test.increment")
        pid = int(self.pid.read_text())
        self.assertTrue(manager.replace_definition(replace(self.definition, trusted=False)).success)
        self.stopped(pid)
        self.assertEqual(old.execute(IntentRequest(old._intent, "increment", 1, arguments={})).error.code, "tool_not_found")
        self.assertEqual(manager.discover("test").error.code, "permission_denied")

    def test_lifecycle_configuration_rejects_invalid_policy(self):
        for change in (
            {"session_mode": "unknown"}, {"startup_mode": "unknown"},
            {"idle_timeout_seconds": 0}, {"idle_timeout_seconds": True},
            {"idle_timeout_seconds": float("nan")},
            {"session_mode": "operation", "startup_mode": "eager"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.definition, **change)

    def test_eager_failure_is_isolated_from_healthy_server(self):
        manager = LocalMCPManager([
            replace(self.definition, startup_mode="eager"),
            LocalServerDefinition("missing", "coda-nonexistent-executable", trusted=True,
                                  session_mode="persistent", startup_mode="eager"),
        ])
        self.addCleanup(manager.close)
        results = manager.start_eager()
        self.assertTrue(results["test"].success)
        self.assertFalse(results["missing"].success)
        self.assertTrue(self.invoke(manager).success)

    def test_retirement_cancels_active_and_queued_calls_without_stale_success(self):
        manager = self.manager(timeout_seconds=10)
        self.assertTrue(manager.discover("test").success)
        pid = int(self.pid.read_text())
        active_results, queued_results = [], []
        active = Thread(target=lambda: active_results.append(self.invoke(manager, "slow", {"delay": 60})))
        queued = Thread(target=lambda: queued_results.append(self.invoke(manager)))
        active.start()
        self.addCleanup(lambda: (manager.retire("test"), active.join(15), queued.join(15)))
        deadline = time.monotonic() + 3
        while not self.started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.started.exists())
        queued.start()
        time.sleep(0.05)
        self.assertTrue(manager.retire("test").success)
        active.join(15)
        queued.join(15)
        self.assertEqual(active_results[0].error.code, "cancelled")
        self.assertEqual(queued_results[0].error.code, "cancelled")
        self.stopped(pid)
