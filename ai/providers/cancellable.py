"""Run blocking provider work without trapping CODA's execution worker."""

from queue import Empty, Queue
from threading import Event, Thread
import time
from typing import Callable, TypeVar


Result = TypeVar("Result")


def run_cancellable(
    work: Callable[[], Result],
    cancel_event: Event | None,
    timeout_seconds: float,
    timeout_message: str,
) -> Result:
    """Return blocking work while polling cancellation and a hard deadline."""
    results = Queue(maxsize=1)

    def run() -> None:
        try:
            results.put((work(), None))
        except Exception as error:  # re-raised on the calling thread
            results.put((None, error))

    Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + timeout_seconds

    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Request cancelled.")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(timeout_message)

        try:
            result, error = results.get(timeout=min(0.05, remaining))
        except Empty:
            continue

        if error is not None:
            raise error

        return result
