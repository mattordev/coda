import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runtime.schedule_approval import propose_schedule, save_confirmed_schedule
from runtime.schedule_store import ScheduleStore
from runtime.system_policy import SystemPolicyError


class ScheduleApprovalTests(unittest.TestCase):
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

    def test_confirmed_edit_cannot_overwrite_a_newer_definition(self):
        task = save_confirmed_schedule(self.store, self.policy, self.propose())
        stale = self.propose(task_id=task, interval=30)
        fresh = self.propose(task_id=task, interval=60)
        self.assertEqual(save_confirmed_schedule(self.store, self.policy, fresh), task)
        with self.assertRaisesRegex(ValueError, "changed"):
            save_confirmed_schedule(self.store, self.policy, stale)
        self.assertEqual(self.store.list_tasks()[0]["interval"], 60)
