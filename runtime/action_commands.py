"""Interactive foreground execution of operator-defined local actions."""

from functools import partial
import re
from threading import Event

from ai.privacy.sanitizer import sanitize_text
from runtime.background_actions import BackgroundActions
from runtime.process_executor import execute_approved_action
from runtime.schedule_commands import configured_policy_path
from runtime.system_policy import load_system_policy

background_actions = BackgroundActions()


def _execute(policy_path, fingerprint, name, background, stop, publish_metrics=None):
    try:
        if configured_policy_path() != policy_path:
            return "The execution policy location changed. Request the action again."
        policy = load_system_policy(policy_path)
        if policy.fingerprint != fingerprint:
            return "The execution policy changed. Request the action again."
        action = policy.action(name, background=background)
        outcome = execute_approved_action(
            action, policy.secrets, stop or Event(), publish_metrics=publish_metrics,
        )
    except (OSError, ValueError):
        return "The approved action could not run. Check the current system policy."
    state = outcome.state.replace("_", " ")
    lines = [f"Action {name} {state}."]
    if outcome.exit_code is not None:
        lines.append(f"Exit code: {outcome.exit_code}.")
    for label, output in (("Standard output", outcome.stdout), ("Standard error", outcome.stderr)):
        if output is None:
            lines.append(f"{label} unavailable.")
        elif output.text or output.dropped_bytes:
            text = sanitize_text(output.text)
            lines.append(f"{label}: {text[:2000]}")
            if len(text) > 2000 or output.dropped_bytes:
                lines.append(f"{label} was truncated.")
    return "\n".join(lines)


def _start_background(work, stop):
    if stop is not None and stop.is_set():
        return "Action cancelled."
    try:
        key = background_actions.submit(work)
    except (ValueError, RuntimeError):
        return "No background action slot is available."
    return f"Background action queued. Job ID: {key}."


def action_command(message):
    """Recognize explicit local action requests before model routing."""
    inspection = re.fullmatch(r"action inspect ([0-9a-f]{32})", message, re.IGNORECASE | re.ASCII)
    if inspection is not None:
        metrics = background_actions.inspect(inspection[1].lower())
        if metrics is None:
            return "No live resource readings for that background job.", None
        return (f"The background program has used {metrics['cpu_seconds']:.2f} CPU seconds "
                f"and uses {metrics['resident_bytes'] / 1024**2:.1f} MiB of resident memory."), None
    if message.casefold() == "action inspect" or message.casefold().startswith("action inspect "):
        return "Supply the background job ID returned by CODA.", None
    job = re.fullmatch(r"action (status|cancel) ([0-9a-f]{32})", message, re.IGNORECASE | re.ASCII)
    if job is not None:
        operation = background_actions.status if job[1].lower() == "status" else background_actions.cancel
        return operation(job[2].lower()), None
    match = re.fullmatch(r"run action ([a-z][a-z0-9_-]{0,63})( in background)?",
                         message, re.IGNORECASE | re.ASCII)
    if match is None:
        if message.casefold().startswith("run action "):
            return "Supply one approved action name without additional arguments.", None
        return None
    name = match[1].lower()
    background = match[2] is not None
    try:
        path = configured_policy_path()
        if path is None:
            return "System execution is disabled.", None
        policy = load_system_policy(path)
        policy.action(name, background=background)
    except (OSError, ValueError):
        return "That action is unavailable under the current system policy.", None
    work = partial(_execute, path, policy.fingerprint, name, background)
    mode = " in the background" if background else ""
    confirm = partial(_start_background, work) if background else work
    return f"Run the approved action {name}{mode}? Say confirm or cancel.", confirm
