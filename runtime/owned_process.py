"""Launch operator-approved programs with owned process-tree cleanup."""

from contextlib import contextmanager, suppress
from io import DEFAULT_BUFFER_SIZE
import os
import signal
import subprocess
import sys


# The supervisor cannot launch the command until its parent assigns it to the job.
_GATED_LAUNCH = """\
import subprocess
import sys
if sys.stdin.buffer.read(1) != b"1":
    sys.exit(125)
try:
    result = subprocess.call(sys.argv[1:], executable=sys.argv[1], stdin=subprocess.DEVNULL)
except OSError:
    print("The approved program could not start.", file=sys.stderr)
    result = 125
sys.exit(result)
"""


@contextmanager
def _windows_process(argv, options):
    import win32api
    import win32con
    import win32job

    # CreateProcess requires a Windows system directory for a minimal environment.
    if not any(name.casefold() == "systemroot" for name in options["env"]):
        options["env"]["SystemRoot"] = win32api.GetWindowsDirectory()
    job = win32job.CreateJobObject(None, "")
    process = None
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", _GATED_LAUNCH, *argv],
            stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW, **options,
        )
        access = win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE
        handle = win32api.OpenProcess(access, False, process.pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            handle.Close()
        process.stdin.write(b"1")
        process.stdin.close()
        yield process
    finally:
        job.Close()
        if process is not None:
            if not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()


@contextmanager
def start_owned_process(action):
    """Start an already validated action and clean up its owned descendants on exit."""
    argv = [action.executable, *action.argv]
    options = {
        "cwd": action.cwd,
        "env": dict(action.environment),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": DEFAULT_BUFFER_SIZE,
    }
    if os.name == "nt":
        with _windows_process(argv, options) as process:
            yield process
        return
    if not sys.platform.startswith("linux"):
        raise OSError("Owned process execution is unavailable on this platform.")
    with subprocess.Popen(argv, executable=argv[0], stdin=subprocess.DEVNULL,
                          start_new_session=True, **options) as process:
        try:
            yield process
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
