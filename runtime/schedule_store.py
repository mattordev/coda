"""Durable schedule state. All times are integer UTC epoch seconds."""

from contextlib import contextmanager
import sqlite3
from uuid import uuid4


class ScheduleStore:
    def __init__(self, path):
        self.path = path
        with self._write() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                db.execute("""CREATE TABLE tasks (
                    id TEXT PRIMARY KEY, action TEXT NOT NULL, binding TEXT NOT NULL,
                    timezone TEXT NOT NULL, due INTEGER, interval INTEGER,
                    CHECK (interval IS NULL OR interval > 0)
                )""")
                db.execute("""CREATE TABLE runs (
                    task_id TEXT NOT NULL REFERENCES tasks(id), due INTEGER NOT NULL,
                    state TEXT NOT NULL, PRIMARY KEY (task_id, due)
                )""")
            if version in (0, 1):
                db.execute("""ALTER TABLE tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'paused', 'cancelled'))""")
            if version in (0, 1, 2):
                db.execute("ALTER TABLE runs ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0")
                db.execute("PRAGMA user_version = 3")
            elif version != 3:
                raise ValueError("Unsupported schedule database version.")

    @contextmanager
    def _write(self):
        db = sqlite3.connect(self.path, timeout=1)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @staticmethod
    def _validate_timing(due, interval):
        if type(due) is not int or not 0 <= due < 2**62:
            raise ValueError("Invalid schedule timestamp.")
        if interval is not None and (type(interval) is not int or not 1 <= interval <= 366 * 86400):
            raise ValueError("Invalid schedule interval.")

    def create(self, action, binding, timezone, due, interval=None):
        """Save a task whose normalized definition has been approved by the host."""
        self._validate_timing(due, interval)
        task_id = uuid4().hex
        with self._write() as db:
            if db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= 128:
                raise ValueError("Schedule task limit reached.")
            db.execute("INSERT INTO tasks (id, action, binding, timezone, due, interval) VALUES (?, ?, ?, ?, ?, ?)",
                       (task_id, action, binding, timezone, due, interval))
        return task_id

    def replace(self, task_id, expected_binding, action, binding, timezone, due, interval=None):
        """Apply a newly approved definition conditionally, preserving paused state."""
        self._validate_timing(due, interval)
        if binding == expected_binding:
            raise ValueError("A replacement requires a new approval binding.")
        with self._write() as db:
            result = db.execute("""UPDATE tasks
                SET action = ?, binding = ?, timezone = ?, due = ?, interval = ?
                WHERE id = ? AND binding = ? AND status != 'cancelled' AND tasks.due IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM runs WHERE task_id = tasks.id
                    AND (state = 'claimed' OR runs.due >= ?)
                  )""", (action, binding, timezone, due, interval, task_id, expected_binding, due))
            return result.rowcount == 1

    def list_tasks(self):
        """Read schedule definitions without exposing their approval bindings."""
        with self._write() as db:
            return [dict(row) for row in db.execute(
                "SELECT id, action, timezone, due, interval, status FROM tasks ORDER BY rowid"
            )]

    def current_binding(self, task_id):
        """Read the revision privately for a conditional edit."""
        with self._write() as db:
            row = db.execute("SELECT binding FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return row["binding"] if row is not None else None

    def pause(self, task_id):
        return self._change_status(task_id, "paused", ("active",))

    def resume(self, task_id):
        return self._change_status(task_id, "active", ("paused",))

    def cancel(self, task_id):
        """Cancel future occurrences. A worker owns any already claimed run."""
        return self._change_status(task_id, "cancelled", ("active", "paused"))

    def _change_status(self, task_id, target, allowed):
        with self._write() as db:
            # A one-time task with no due date has already been consumed.
            result = db.execute("""UPDATE tasks SET status = ?,
                due = CASE WHEN ? = 'cancelled' THEN NULL ELSE due END
                WHERE id = ? AND due IS NOT NULL AND status IN (?, ?)""",
                (target, target, task_id, allowed[0], allowed[-1]))
            return result.rowcount == 1

    def claim(self, now, *, grace):
        """Record and advance one occurrence atomically before handing it to a worker."""
        with self._write() as db:
            task = db.execute("""SELECT tasks.* FROM tasks
                WHERE status = 'active' AND due <= ? AND NOT EXISTS (
                    SELECT 1 FROM runs WHERE task_id = tasks.id AND state = 'claimed'
                ) ORDER BY due, id LIMIT 1""", (now,)).fetchone()
            if task is None:
                return None
            due, interval = task["due"], task["interval"]
            if interval is not None:
                # Select the latest elapsed occurrence without replaying backlog.
                due += ((now - due) // interval) * interval
            state = "missed" if now - due > grace else "claimed"
            db.execute("INSERT INTO runs (task_id, due, state) VALUES (?, ?, ?)",
                       (task["id"], due, state))
            db.execute("UPDATE tasks SET due = ? WHERE id = ?",
                       (None if interval is None else due + interval, task["id"]))
            self._prune(db)
        return {**dict(task), "due": due, "state": state}

    def finish(self, task_id, due, state):
        if state not in {"completed", "failed", "denied", "cancelled", "interrupted"}:
            raise ValueError("Invalid terminal run state.")
        with self._write() as db:
            result = db.execute("""UPDATE runs SET state = ?
                WHERE task_id = ? AND due = ? AND state = 'claimed'""", (state, task_id, due))
            self._prune(db)
            return result.rowcount == 1

    def recover_interrupted(self):
        """Call at startup only after establishing exclusive worker ownership."""
        with self._write() as db:
            count = db.execute("UPDATE runs SET state = 'interrupted' WHERE state = 'claimed'").rowcount
            self._prune(db)
            return count

    def history(self):
        """Read retained occurrence records, newest first."""
        with self._write() as db:
            return [dict(row) for row in db.execute(
                "SELECT task_id, due, state FROM runs ORDER BY rowid DESC"
            )]

    def pending_attention(self):
        """Return a bounded batch of terminal occurrences awaiting acknowledgement."""
        with self._write() as db:
            return [dict(row) for row in db.execute("""SELECT task_id, due, state
                FROM runs WHERE state != 'claimed' AND acknowledged = 0
                ORDER BY rowid LIMIT 25""")]

    def acknowledge(self, task_id, due):
        """Mark a reported outcome as seen without changing its execution history."""
        with self._write() as db:
            result = db.execute("""UPDATE runs SET acknowledged = 1
                WHERE task_id = ? AND due = ? AND state != 'claimed' AND acknowledged = 0""",
                (task_id, due))
            return result.rowcount == 1

    @staticmethod
    def _prune(db):
        db.execute("""DELETE FROM runs WHERE rowid IN (
            SELECT rowid FROM runs WHERE state != 'claimed'
            ORDER BY rowid DESC LIMIT -1 OFFSET 512
        )""")
