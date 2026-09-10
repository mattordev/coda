from __future__ import annotations

"""Run blocking provider work without trapping CODA's execution worker."""

from queue import Empty, Queue
from threading import Event, Lock, Thread
import time
from typing import TYPE_CHECKING, ParamSpec, TypeVar

if TYPE_CHECKING:
    from typing import Callable, Concatenate


Result = TypeVar("Result")

_P = ParamSpec("_P")

CANCELLATION_MESSAGE = "Request cancelled."


class CancellationScope:
    """Own cleanup callbacks for provider resources created by worker code."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._cancelled = False

    def add(self, callback: Callable[[], None]) -> Callable[[], None]:
        callback_lock = Lock()
        called = False

        def call_once() -> None:
            nonlocal called
            with callback_lock:
                if called:
                    return
                called = True
            callback()

        with self._lock:
            if not self._cancelled:
                self._callbacks.append(call_once)
                return call_once

        call_once()
        return call_once

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            callbacks = list(reversed(self._callbacks))
            self._callbacks.clear()

        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue

    def run_cancellable(
        self,
        function: Callable[Concatenate[CancellationScope, Event | None, _P], Result],
        cancel_event: Event | None,
        timeout_seconds: float,
        timeout_message: str,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> Result | None:
        """Return blocking work while polling cancellation and a hard deadline."""
        results: Queue[tuple[Result | None, Exception | None]] = Queue(maxsize=1)

        def run() -> None:
            try:
                results.put((function(self, cancel_event, *args, **kwargs), None))
            except Exception as error:  # re-raised on the calling thread
                results.put((None, error))

        Thread(target=run, daemon=True).start()
        deadline = time.monotonic() + timeout_seconds

        while True:
            if cancel_event is not None and cancel_event.is_set():
                self.cancel()
                raise InterruptedError("Request cancelled.")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.cancel()
                raise TimeoutError(timeout_message)

            try:
                result, error = results.get(timeout=min(0.05, remaining))
            except Empty:
                continue

            if error is not None:
                raise error

            return result
