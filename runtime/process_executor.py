"""Execute an approved local action with bounded output and cancellation."""

from dataclasses import dataclass
import os
from threading import Thread
from time import monotonic

from runtime.owned_process import start_owned_process
from runtime.process_output import ProcessOutput, capture_output


@dataclass(frozen=True)
class ProcessResult:
    state: str
    exit_code: int | None = None
    stdout: ProcessOutput | None = None
    stderr: ProcessOutput | None = None


def execute_approved_action(action, secrets, stop):
    """Run on a worker thread. The caller supplies the current trusted grant."""
    if stop.is_set():
        return ProcessResult("cancelled")
    results = {}
    readers = []

    def drain(name, stream):
        with stream:
            results[name] = capture_output(stream, action.max_output_bytes, secrets)

    deadline = monotonic() + action.timeout_seconds
    with start_owned_process(action) as process:
        for name in ("stdout", "stderr"):
            # Own the read handles independently so tree cleanup can close Popen.
            stream = os.fdopen(os.dup(getattr(process, name).fileno()), "rb")
            reader = Thread(target=drain, args=(name, stream), daemon=True)
            readers.append(reader)
            reader.start()
        while (exit_code := process.poll()) is None:
            if stop.is_set():
                state = "cancelled"
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                state = "timed_out"
                break
            stop.wait(min(remaining, 0.05))
        else:
            state = "completed" if exit_code == 0 else "failed"

    for reader in readers:
        reader.join(timeout=1)
    return ProcessResult(state, exit_code, results.get("stdout"), results.get("stderr"))
