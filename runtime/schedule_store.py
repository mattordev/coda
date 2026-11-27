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
                db.execute("PRAGMA user_version = 1")
            elif version != 1:
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

    def create(self, action, binding, timezone, due, interval=None):
        """Save a task whose normalized definition has been approved by the host."""
        if type(due) is not int or not 0 <= due < 2**62:
            raise ValueError("Invalid schedule timestamp.")
        if interval is not None and (type(interval) is not int or not 1 <= interval <= 366 * 86400):
            raise ValueError("Invalid schedule interval.")
        task_id = uuid4().hex
        with self._write() as db:
            if db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= 128:
                raise ValueError("Schedule task limit reached.")
            db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                       (task_id, action, binding, timezone, due, interval))
        return task_id

    def claim(self, now, *, grace):
        """Record and advance one occurrence atomically before handing it to a worker."""
        with self._write() as db:
            task = db.execute("""SELECT tasks.* FROM tasks
                WHERE due <= ? AND NOT EXISTS (
                    SELECT 1 FROM runs WHERE task_id = tasks.id AND state = 'claimed'
                ) ORDER BY due, id LIMIT 1""", (now,)).fetchone()
            if task is None:
                return None
            due, interval = task["due"], task["interval"]
            if interval is not None:
                # Select the latest elapsed occurrence without replaying backlog.
                due += ((now - due) // interval) * interval
            state = "missed" if now - due > grace else "claimed"
            db.execute("INSERT INTO runs VALUES (?, ?, ?)", (task["id"], due, state))
            db.execute("UPDATE tasks SET due = ? WHERE id = ?",
                       (None if interval is None else due + interval, task["id"]))
            self._prune(db)
        return {**dict(task), "due": due, "state": state}

    def finish(self, task_id, due, state):
        if state not in {"completed", "failed", "denied", "cancelled"}:
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

    @staticmethod
    def _prune(db):
        db.execute("""DELETE FROM runs WHERE rowid IN (
            SELECT rowid FROM runs WHERE state != 'claimed'
            ORDER BY rowid DESC LIMIT -1 OFFSET 512
        )""")
