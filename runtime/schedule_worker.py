"""Claim one scheduled occurrence and revalidate its grant before dispatch."""

from runtime.system_policy import load_system_policy


def run_due_schedule(store, policy_path, execute, *, now, grace):
    """Run one tick on the scheduler thread using the host's trusted executor.

    The executor receives an operator action and its redaction values. It returns
    a definitive terminal state. An exception leaves the external effect unknown.
    """
    occurrence = store.claim(now, grace=grace)
    if occurrence is None or occurrence["state"] == "missed":
        return occurrence

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
            if state not in ("completed", "failed", "cancelled"):
                state = "interrupted"
        except Exception:
            state = "interrupted"
    store.finish(occurrence["id"], occurrence["due"], state)
    return {"id": occurrence["id"], "due": occurrence["due"], "state": state}
