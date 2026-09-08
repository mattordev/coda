"""Standard-library-only environment selection and startup handoff."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

ACTIVE_ENVIRONMENT = ".coda-update-active.json"
HANDOFF_ENV = "CODA_UPDATE_HANDOFF"
HANDOFF_TIMEOUT = 60


def _plain_path(path):
    if path.is_symlink() or (
        path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400
    ):
        raise ValueError(f"Managed update path must not be a link: {path}")
    return path


def environment_python(environment):
    """Return the interpreter path without relocating the environment."""
    scripts = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return environment / scripts / executable


def managed_environment(root, relative):
    """Accept only an updater-created environment directly under this install."""
    parts = Path(relative).parts
    if len(parts) != 2 or not parts[0].startswith(".coda-update-") or parts[1] != "environment":
        raise ValueError("Invalid managed update environment")
    environment = _plain_path(_plain_path(root / parts[0]) / parts[1])
    python = environment_python(environment)
    _plain_path(python.parent)
    _plain_path(python)
    if not python.is_file() or not (environment / "pyvenv.cfg").is_file():
        raise ValueError(f"Managed update environment is incomplete: {environment}")
    return python


def selected_python(root):
    marker = _plain_path(root / ACTIVE_ENVIRONMENT)
    if not marker.exists():
        return None
    with marker.open(encoding="utf-8") as handle:
        selection = json.load(handle)
    return managed_environment(root, selection["environment"])


def launch_environment(python):
    """Keep configuration/cache settings, but remove stale Python activation."""
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["VIRTUAL_ENV"] = str(python.parent.parent)
    environment["PATH"] = str(python.parent) + os.pathsep + environment.get("PATH", "")
    return environment


def wait_for_process(process):
    """Retain the foreground terminal until the replacement exits."""
    try:
        return process.wait()
    except KeyboardInterrupt:
        # The child normally receives the same console interrupt. Give its
        # runtime finally block time to stop audio and workers before terminating.
        try:
            return process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                return process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                return process.wait(timeout=6)


def _interpreter_path(python):
    # Canonicalize directory aliases (including Windows 8.3 paths), but not
    # the executable itself: POSIX venvs may link to the same base Python.
    return python.parent.resolve() / python.name


def redirect_to_selected_environment(root, arguments):
    python = selected_python(root)
    if python is None or _interpreter_path(python) == _interpreter_path(Path(sys.executable)):
        return None
    process = subprocess.Popen(
        [str(python), str(root / "main.py"), *arguments],
        cwd=root, env=launch_environment(python),
    )
    return wait_for_process(process)


def acknowledge_handoff(root):
    """Signal successful startup setup, then wait before starting any workers."""
    attempt_name = os.environ.pop(HANDOFF_ENV, None)
    if attempt_name is None:
        return False
    python = managed_environment(root, str(Path(attempt_name) / "environment"))
    if _interpreter_path(python) != _interpreter_path(Path(sys.executable)):
        raise ValueError("Update handoff is not running in the staged environment")
    workspace = python.parent.parent.parent
    ready = _plain_path(workspace / "restart-ready")
    proceed = _plain_path(workspace / "restart-proceed")
    with ready.open("x", encoding="utf-8") as handle:
        handle.write("ready")
    deadline = time.monotonic() + HANDOFF_TIMEOUT
    while time.monotonic() < deadline:
        if proceed.is_file():
            return True
        time.sleep(0.05)
    raise TimeoutError("Update parent did not complete the startup handoff")
