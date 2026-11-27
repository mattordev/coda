from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime.schedule_store import ScheduleStore


class ScheduleStoreTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "scheduler.sqlite"
        self.store = ScheduleStore(self.path)

    def test_pause_resume_and_cancel_survive_reopen(self):
        task = self.store.create("probe", "private-binding", "UTC", 100, interval=10)
        self.assertTrue(self.store.pause(task))
        reopened = ScheduleStore(self.path)
        self.assertIsNone(reopened.claim(100, grace=0))
        self.assertEqual(reopened.list_tasks()[0]["status"], "paused")
        self.assertNotIn("binding", reopened.list_tasks()[0])
        self.assertTrue(reopened.resume(task))
        self.assertEqual(reopened.claim(100, grace=0)["state"], "claimed")
        self.assertTrue(reopened.cancel(task))
        self.assertTrue(reopened.finish(task, 100, "completed"))
        reopened = ScheduleStore(self.path)
        self.assertFalse(reopened.resume(task))
        self.assertIsNone(reopened.claim(110, grace=0))
        self.assertEqual(reopened.list_tasks()[0]["status"], "cancelled")

    def test_competing_workers_cannot_claim_the_same_occurrence(self):
        task_id = self.store.create("probe", "binding", "UTC", 100, interval=10)
        other = ScheduleStore(self.path)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(store.claim, 100, grace=0) for store in (self.store, other)]
            claims = [claim for future in futures if (claim := future.result()) is not None]
        self.assertEqual(len(claims), 1)
        self.assertIsNone(other.claim(110, grace=0))
        self.assertTrue(self.store.finish(task_id, 100, "completed"))
        self.assertEqual(other.claim(110, grace=0)["due"], 110)

    def test_restart_records_unknown_effect_without_replaying_one_time_work(self):
        task_id = self.store.create("probe", "binding", "UTC", 100)
        self.assertEqual(self.store.claim(100, grace=0)["state"], "claimed")
        reopened = ScheduleStore(self.path)
        self.assertEqual(reopened.recover_interrupted(), 1)
        self.assertIsNone(reopened.claim(105, grace=10))
        self.assertFalse(reopened.finish(task_id, 100, "completed"))
        self.assertEqual(reopened.history(), [{"task_id": task_id, "due": 100, "state": "interrupted"}])

    def test_old_recurring_ticks_do_not_replay(self):
        self.store.create("probe", "binding", "UTC", 100, interval=10)
        missed = self.store.claim(134, grace=2)
        self.assertEqual((missed["due"], missed["state"]), (130, "missed"))
        self.assertIsNone(self.store.claim(134, grace=2))
        self.assertEqual(self.store.claim(141, grace=2)["due"], 140)
