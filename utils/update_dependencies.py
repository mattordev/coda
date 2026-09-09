"""Prepare update dependencies without altering the running environment.

The returned environment stays at its original path: virtual environments are
not relocatable. The update/restart coordinator owns activation and recovery.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from utils.update_progress import UpdateProgress


ENVIRONMENT_DIR = "environment"
LOG_NAME = "dependency-install.log"
VENV_TIMEOUT = 120
INSTALL_TIMEOUT = 1200
CHECK_TIMEOUT = 120


class DependencyPreparationError(RuntimeError):
    """An isolated dependency environment could not be prepared."""


def environment_python(environment: Path) -> Path:
    """Return the platform-specific interpreter in a virtual environment."""
    if sys.platform == "win32":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _reject_link(path: Path) -> None:
    if path.is_symlink() or (
        path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400
    ):
        raise ValueError(f"Dependency staging path must not be a link or junction: {path}")


def _run_step(command, step, timeout, workspace, process_env, log):
    # Keep potentially sensitive package-index output in the local recovery log,
    # never interpolate captured pip output into a runtime/console exception.
    log.write(f"\n--- {step} ---\n".encode("utf-8"))
    log.flush()
    try:
        with UpdateProgress(f"Dependencies: {step}", budget=timeout, estimate=timeout / 4):
            subprocess.run(
                command,
                check=True,
                timeout=timeout,
                cwd=str(workspace),
                env=process_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    except (OSError, subprocess.SubprocessError) as error:
        reason = "timed out" if isinstance(error, subprocess.TimeoutExpired) else "failed"
        raise DependencyPreparationError(
            f"Update dependency {step} {reason}. "
            f"The current environment is unchanged; inspect {workspace / LOG_NAME}."
        ) from error


def prepare_environment(workspace: Path, staged: Path, python_executable: str) -> Path:
    """Create and verify a fresh environment from the staged requirements.

    ``workspace`` must be this attempt's existing private recovery directory and
    ``staged`` must be inside it. Never pass the currently active environment as
    a destination. Failed environments and logs are retained for investigation.
    This installs packages only; model downloads remain a normal first-use step.
    """
    workspace = Path(workspace)
    staged = Path(staged)
    _reject_link(workspace)
    _reject_link(staged)
    workspace = workspace.resolve(strict=True)
    staged = staged.resolve(strict=True)
    if not workspace.is_dir() or not staged.is_dir() or not staged.is_relative_to(workspace):
        raise ValueError("Dependency source must be a staged directory within the update workspace")

    requirements = staged / "requirements.txt"
    _reject_link(requirements)
    if not requirements.is_file():
        raise FileNotFoundError(f"Downloaded update is missing requirements.txt: {requirements}")
    executable = Path(python_executable).resolve(strict=True)
    if not executable.is_file():
        raise ValueError("Dependency bootstrap interpreter must be a file")

    environment = workspace / ENVIRONMENT_DIR
    _reject_link(environment)
    # Refuse an existing destination even if empty: it may belong to another
    # attempt or be an active environment. No reuse, cleanup, or in-place repair.
    environment.mkdir()
    interpreter = environment_python(environment)
    process_env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        process_env.pop(name, None)
    # --isolated ignores user/environment options, but global pip configuration
    # is still read unless explicitly disabled. A global target/prefix must not
    # redirect installation into the active environment or another directory.
    process_env["PIP_CONFIG_FILE"] = os.devnull

    with (workspace / LOG_NAME).open("xb") as log:
        _run_step(
            [str(executable), "-I", "-m", "venv", "--copies", str(environment)],
            "environment creation", VENV_TIMEOUT, workspace, process_env, log,
        )
        _reject_link(interpreter)
        if not interpreter.is_file():
            raise DependencyPreparationError(
                f"Update environment has no Python interpreter; inspect {workspace / LOG_NAME}."
            )
        process_env = dict(process_env, PATH=str(interpreter.parent) + os.pathsep + process_env.get("PATH", ""))
        _run_step(
            [str(interpreter), "-I", "-m", "pip", "--isolated", "--require-virtualenv", "install",
             "--disable-pip-version-check", "--no-input", "--no-user", "-r", str(requirements)],
            "installation", INSTALL_TIMEOUT, workspace, process_env, log,
        )
        _run_step(
            [str(interpreter), "-I", "-m", "pip", "--isolated", "--require-virtualenv", "check"],
            "verification", CHECK_TIMEOUT, workspace, process_env, log,
        )

    if shutil.which("ffplay") is None:
        print(
            "Update warning: ffplay was not found on PATH. Install FFmpeg with ffplay "
            "for Pocket TTS or ElevenLabs playback; pyttsx3 remains available without it."
        )
    return interpreter
