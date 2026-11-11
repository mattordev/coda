"""Debug-only stages and elapsed time; never manufacture model-load percentages."""
from threading import Event, Lock, Thread
import time

from utils import runtime_state


class ModelActivity:
    """Report slow preparation without exposing prompts, paths or server errors."""

    def __init__(self, label, stage, *, cancel_event=None, interval=5):
        self.label = label
        self._stage = stage
        self._cancel = cancel_event
        self._interval = interval
        self._stop = Event()
        self._lock = Lock()
        self._worker = None
        self._started = None
        self._finished = False

    def __enter__(self):
        self._started = time.monotonic()
        if runtime_state.is_debug_enabled():
            self._report(self._stage)
            self._worker = Thread(target=self._pulse, name='model-status', daemon=True)
            self._worker.start()
        return self

    def _report(self, stage):
        runtime_state.debug_print(
            f'[MODEL] {self.label}: {stage} ({time.monotonic() - self._started:.1f}s elapsed)',
            flush=True,
        )

    def _pulse(self):
        while not self._stop.wait(self._interval):
            with self._lock:
                if self._finished or (self._cancel is not None and self._cancel.is_set()):
                    return
                self._report(self._stage)

    def stage(self, stage):
        with self._lock:
            if not self._finished:
                self._stage = stage
                self._report(stage)

    def finish(self, outcome='ready'):
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._stop.set()
            if self._cancel is not None and self._cancel.is_set():
                outcome = 'cancelled'
            self._report(outcome)

    def __exit__(self, exc_type, _exc, _traceback):
        self.finish('cancelled' if exc_type is InterruptedError else 'failed' if exc_type else 'ready')
        if self._worker is not None:
            self._worker.join(timeout=0.2)
