"""Claim one scheduled occurrence and revalidate its grant before dispatch."""

from contextlib import contextmanager
from pathlib import Path
import os
from threading import Event, Thread
from time import time
import sqlite3

from runtime.schedule_store import ScheduleStore
from runtime.process_executor import execute_approved_action
from runtime.system_policy import load_system_policy


def run_due_schedule(store, policy_path, execute, *, now, grace):
    """Run one tick on the scheduler thread using the host's trusted executor.

    The executor receives an operator action and its redaction values. It returns
    a definitive terminal state. An exception leaves the external effect unknown.
    """
    occurrence = store.claim(now, grace=grace)
    if occurrence is None:
        return None
    if occurrence["state"] == "missed":
        return {key: occurrence[key] for key in ("id", "due", "state")}

    try:
        policy = load_system_policy(policy_path)
        action = policy.action(occurrence["action"], scheduled=True)
        approved_policy, separator, revision = occurrence["binding"].partition(":")
        allowed = bool(separator and revision and approved_policy == policy.fingerprint)
    except (OSError, ValueError):
        allowed = False

    state = "denied"
    if allowed:
        try:
            state = execute(action, policy.secrets)
            if state not in ("completed", "failed", "cancelled", "timed_out"):
                state = "interrupted"
        except Exception:
            state = "interrupted"
    store.finish(occurrence["id"], occurrence["due"], state)
    return {"id": occurrence["id"], "due": occurrence["due"], "state": state}


class SchedulerAlreadyRunning(RuntimeError):
    """Another worker holds the schedule database's lifetime lock."""


@contextmanager
def scheduler_ownership(database_path):
    """Hold an OS lock until exit or process death. Keep the lock file in place."""
    path = Path(database_path).resolve()
    with path.with_name(path.name + ".worker.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SchedulerAlreadyRunning("Could not acquire scheduler ownership.") from exc
        yield


def run_scheduler(store, policy_path, execute, stop, *, grace=30, notify=None):
    """Run on a service thread, recovering interrupted work once per ownership."""
    with scheduler_ownership(store.path):
        if stop.is_set():
            return
        store.recover_interrupted()
        while not stop.is_set():
            outcome = run_due_schedule(store, policy_path, execute, now=int(time()), grace=grace)
            if outcome is not None and notify is not None:
                notify(outcome)
            stop.wait(0.25)


def _execute_scheduled_action(action, secrets, stop):
    return execute_approved_action(action, secrets, stop).state


class SchedulerService:
    """Own the scheduler thread and its shutdown signal."""

    def __init__(self, database_path, policy_path, execute=_execute_scheduled_action, *, notify=None):
        self._stop = Event()
        self._thread = Thread(
            target=self._run, args=(database_path, policy_path, execute, notify),
            name="coda-scheduler", daemon=True,
        )
        self.failure = None

    def _run(self, database_path, policy_path, execute, notify):
        try:
            store = ScheduleStore(database_path)
            run_scheduler(
                store, policy_path,
                lambda action, secrets: execute(action, secrets, self._stop),
                self._stop, notify=notify,
            )
        except SchedulerAlreadyRunning:
            self.failure = "Scheduler ownership is unavailable."
        except (sqlite3.Error, OSError, ValueError):
            self.failure = "Schedule storage is unavailable."

    def start(self):
        self._thread.start()

    def close(self, timeout=5):
        """Signal the executor as well as the idle loop and wait for ownership release."""
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout)
        return not self._thread.is_alive()
