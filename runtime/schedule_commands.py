"""Deterministic local schedule commands and their confirmation callbacks."""

from datetime import UTC, datetime
from functools import partial
import json
import os
from pathlib import Path
import sqlite3

from runtime.schedule_approval import propose_schedule, save_confirmed_schedule
from runtime.schedule_store import ScheduleStore


def configured_policy_path():
    configured = os.environ.get("CODA_SYSTEM_POLICY")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("CODA_SYSTEM_POLICY must point to an existing absolute file.")
    return path.resolve()


def _save(store, policy, proposal, stop):
    if stop is not None and stop.is_set():
        return "Action cancelled."
    try:
        if configured_policy_path() != policy:
            return "The scheduler policy location changed. Request the schedule again."
        task = save_confirmed_schedule(store, policy, proposal)
        return f"Schedule saved. Task ID: {task}."
    except (OSError, ValueError, sqlite3.Error):
        return "The schedule could not be saved. Check its time, approval, and current policy."


def schedule_command(message):
    """Return response text and an optional callback for host confirmation."""
    prefix, _, command = message.partition(" ")
    if prefix.casefold() != "schedule":
        return None
    verb, _, payload = command.strip().partition(" ")
    verb = verb.casefold()
    if verb not in {"list", "history", "pending", "create", "edit", "pause", "resume", "cancel", "acknowledge"}:
        return None
    if len(payload) > 4096:
        return "The schedule command is too long.", None
    try:
        policy = configured_policy_path()
        if policy is None:
            return "Scheduling is disabled.", None
        store = ScheduleStore(policy.with_suffix(".schedules.sqlite"))
        if verb in {"list", "history", "pending"}:
            view = "tasks" if verb == "list" else verb
            rows = store.inspect(store.path, view)
            return json.dumps(rows, indent=2) if rows else "No schedule records.", None
        if verb in {"pause", "resume", "cancel"}:
            changed = getattr(store, verb)(payload.strip())
            return ("Schedule updated." if changed else "No matching schedule could be updated."), None
        if verb == "acknowledge":
            task, due = payload.split()
            changed = store.acknowledge(task, int(due))
            return ("Outcome acknowledged." if changed else "No matching pending outcome."), None
        fields = json.loads(payload)
        if not isinstance(fields, dict) or set(fields) - {"action", "local_time", "timezone", "interval", "task_id"}:
            raise ValueError("Invalid schedule fields.")
        if (verb == "edit") != (fields.get("task_id") is not None):
            raise ValueError("Schedule edits require a task ID.")
        proposal = propose_schedule(store, policy, **fields)
        due = datetime.fromtimestamp(proposal.due, UTC).isoformat()
        interval = "once" if proposal.interval is None else f"every {proposal.interval} seconds"
        target = f" for task {proposal.task_id}" if proposal.task_id else ""
        return (f"Confirm {verb}{target}: run {proposal.action} {interval}, starting at {due}? "
                "Say confirm or cancel."), partial(_save, store, policy, proposal)
    except (OSError, OverflowError, TypeError, ValueError, sqlite3.Error):
        return "Could not prepare the schedule command. Check its fields, time, and policy.", None
