"""Host-side schedule proposals for the interactive confirmation flow."""

from dataclasses import dataclass
from time import time
from uuid import uuid4

from runtime.schedule_time import schedule_timestamp
from runtime.system_policy import SystemPolicyError, load_system_policy


@dataclass(frozen=True)
class ScheduleProposal:
    action: str
    timezone: str
    due: int
    interval: int | None
    policy_fingerprint: str
    task_id: str | None
    previous_binding: str | None


def propose_schedule(store, policy_path, *, action, local_time, timezone, interval=None, task_id=None):
    """Capture the exact normalized definition and current authority for confirmation."""
    due = schedule_timestamp(local_time, timezone)
    if due <= time():
        raise ValueError("Choose a future schedule time.")
    if interval is not None and (type(interval) is not int or not 1 <= interval <= 366 * 86400):
        raise ValueError("Invalid schedule interval.")
    policy = load_system_policy(policy_path)
    policy.action(action, scheduled=True)
    previous = store.current_binding(task_id) if task_id is not None else None
    if task_id is not None and previous is None:
        raise ValueError("Schedule task does not exist.")
    return ScheduleProposal(action, timezone, due, interval, policy.fingerprint, task_id, previous)


def save_confirmed_schedule(store, policy_path, proposal):
    """Commit a proposal after the host has consumed its interactive confirmation."""
    policy = load_system_policy(policy_path)
    policy.action(proposal.action, scheduled=True)
    if policy.fingerprint != proposal.policy_fingerprint:
        raise SystemPolicyError("The execution policy changed. Confirm the schedule again.")
    if proposal.due <= time():
        raise ValueError("The proposed schedule time has passed.")
    binding = f"{policy.fingerprint}:{uuid4().hex}"
    fields = (proposal.action, binding, proposal.timezone, proposal.due, proposal.interval)
    if proposal.task_id is None:
        return store.create(*fields)
    if not store.replace(proposal.task_id, proposal.previous_binding, *fields):
        raise ValueError("The task changed or is no longer editable.")
    return proposal.task_id
