"""Interactive foreground execution of operator-defined local actions."""

from functools import partial
import re
from threading import Event

from ai.privacy.sanitizer import sanitize_text
from runtime.process_executor import execute_approved_action
from runtime.schedule_commands import configured_policy_path
from runtime.system_policy import load_system_policy


def _execute(policy_path, fingerprint, name, stop):
    try:
        if configured_policy_path() != policy_path:
            return "The execution policy location changed. Request the action again."
        policy = load_system_policy(policy_path)
        if policy.fingerprint != fingerprint:
            return "The execution policy changed. Request the action again."
        action = policy.action(name)
        outcome = execute_approved_action(action, policy.secrets, stop or Event())
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


def action_command(message):
    """Recognize an explicit foreground request before model routing."""
    match = re.fullmatch(r"run action ([a-z][a-z0-9_-]{0,63})", message, re.IGNORECASE | re.ASCII)
    if match is None:
        if message.casefold().startswith("run action "):
            return "Supply one approved action name without additional arguments.", None
        return None
    name = match[1].lower()
    try:
        path = configured_policy_path()
        if path is None:
            return "System execution is disabled.", None
        policy = load_system_policy(path)
        policy.action(name)
    except (OSError, ValueError):
        return "That action is unavailable under the current system policy.", None
    return f"Run the approved action {name}? Say confirm or cancel.", partial(_execute, path, policy.fingerprint, name)
