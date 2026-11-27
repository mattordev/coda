from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sqlite3
import sys
import unittest

from runtime.schedule_store import ScheduleStore


class ScheduleStoreTests(unittest.TestCase):
    def test_completed_one_time_task_frees_a_slot_without_losing_history(self):
        first = self.store.create("probe", "binding", "UTC", 100)
        for _ in range(127):
            self.store.create("probe", "binding", "UTC", 200)
        with self.assertRaisesRegex(ValueError, "limit"):
            self.store.create("probe", "binding", "UTC", 300)
        self.assertEqual(self.store.claim(100, grace=0)["id"], first)
        self.assertTrue(self.store.finish(first, 100, "completed"))
        self.store.create("probe", "binding", "UTC", 300)
        self.assertEqual(self.store.history(), [
            {"task_id": first, "due": 100, "state": "completed"},
        ])

    def test_migrates_v2_with_paused_task_and_existing_history(self):
        path = self.path.with_name("legacy.sqlite")
        db = sqlite3.connect(path)
        try:
            with db:
                db.executescript("""
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY, action TEXT NOT NULL, binding TEXT NOT NULL,
                        timezone TEXT NOT NULL, due INTEGER, interval INTEGER,
                        status TEXT NOT NULL DEFAULT 'active'
                    );
                    CREATE TABLE runs (
                        task_id TEXT NOT NULL REFERENCES tasks(id), due INTEGER NOT NULL,
                        state TEXT NOT NULL, PRIMARY KEY (task_id, due)
                    );
                    INSERT INTO tasks VALUES ('task', 'probe', 'binding', 'UTC', 200, 100, 'paused');
                    INSERT INTO runs VALUES ('task', 100, 'completed');
                    PRAGMA user_version = 2;
                """)
        finally:
            db.close()
        migrated = ScheduleStore(path)
        self.assertIsNone(migrated.claim(200, grace=0))
        self.assertEqual(migrated.list_tasks()[0]["status"], "paused")
        self.assertEqual(migrated.pending_attention(), [
            {"task_id": "task", "due": 100, "state": "completed"},
        ])
        self.assertTrue(migrated.acknowledge("task", 100))
        self.assertEqual(ScheduleStore(path).pending_attention(), [])

    def test_headless_inspection_reports_only_public_task_fields(self):
        task = self.store.create("probe", "private-binding", "UTC", 100)
        command = [sys.executable, "-m", "runtime.schedule_inspect"]
        report = subprocess.run([*command, str(self.path), "tasks"],
                                capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(json.loads(report.stdout)[0]["id"], task)
        self.assertNotIn("private-binding", report.stdout)
        missing = self.path.with_name("missing.sqlite")
        error = subprocess.run([*command, str(missing), "tasks"],
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(error.returncode, 1)
        self.assertFalse(missing.exists())

    def test_attention_survives_restart_and_acknowledgement_does_not_replay(self):
        task = self.store.create("probe", "binding", "UTC", 100)
        self.store.claim(100, grace=0)
        self.assertEqual(self.store.pending_attention(), [])
        self.assertFalse(self.store.acknowledge(task, 100))
        self.assertTrue(self.store.finish(task, 100, "failed"))
        reopened = ScheduleStore(self.path)
        notice = {"task_id": task, "due": 100, "state": "failed"}
        self.assertEqual(reopened.pending_attention(), [notice])
        self.assertTrue(reopened.acknowledge(task, 100))
        self.assertFalse(reopened.acknowledge(task, 100))
        self.assertEqual(ScheduleStore(self.path).pending_attention(), [])
        self.assertEqual(reopened.history(), [notice])
        self.assertIsNone(reopened.claim(100, grace=0))

    def test_replacement_invalidates_old_definition_and_keeps_pause(self):
        task = self.store.create("probe", "old", "UTC", 100)
        self.assertTrue(self.store.pause(task))
        self.assertTrue(self.store.replace(task, "old", "other", "new", "UTC", 200))
        self.assertFalse(self.store.replace(task, "old", "probe", "stale", "UTC", 150))
        self.assertIsNone(self.store.claim(200, grace=0))
        self.assertTrue(self.store.resume(task))
        self.assertIsNone(self.store.claim(100, grace=0))
        claim = self.store.claim(200, grace=0)
        self.assertEqual((claim["action"], claim["binding"]), ("other", "new"))

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
