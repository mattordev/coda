"""One bounded, cancellable producer/consumer channel per spoken answer."""
from queue import Empty, Full, Queue
from threading import Event, Lock
import time

from tts.incremental import SpeakableChunks


class IncrementalSpeech:
    """Only the speech worker segments/normalises tokens; generation may block.

    Eight 256-character deltas bound the backlog independently of answer size.
    Closing successfully drains in order. Aborting discards everything unspoken.
    Both ends poll the request cancellation event, including a blocked producer.
    """
    def __init__(self, request_id, cancel_event, start, *, capacity=8,
                 accumulator_factory=SpeakableChunks, clock=time.monotonic):
        self.request_id = request_id
        self.cancel_event = cancel_event
        self._start = start
        self._queue = Queue(maxsize=capacity)
        self._done = Event()
        self._aborted = Event()
        self._consumer = Event()
        self._start_lock = Lock()
        self._accumulator_factory = accumulator_factory
        self.clock = clock
        self.started = False
        self.succeeded = False

    def feed(self, text):
        if not text:
            return
        self._check_open()
        with self._start_lock:
            self._check_open()
            if not self.started:
                self.started = True
                self._start(self)
        for offset in range(0, len(text), 256):
            while True:
                self._check_open()
                try:
                    self._queue.put(text[offset:offset+256], timeout=0.05)
                    break
                except Full:
                    continue

    def finish(self, success=True):
        self.succeeded = success and not self.cancel_event.is_set() and not self._aborted.is_set()
        if not self.succeeded:
            self._aborted.set()
            self._discard()
        self._done.set()

    def abort(self):
        self.finish(False)

    def _check_open(self):
        if self.cancel_event.is_set() or self._aborted.is_set() or self._done.is_set():
            raise InterruptedError("Request cancelled.")

    def _discard(self):
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                return

    def __iter__(self):
        if self._consumer.is_set():
            raise RuntimeError("A speech stream has exactly one consumer.")
        self._consumer.set()
        accumulator = self._accumulator_factory()
        while not self.cancel_event.is_set() and not self._aborted.is_set():
            try:
                delta = self._queue.get(timeout=0.05)
            except Empty:
                if self._done.is_set():
                    for chunk in accumulator.finish():
                        if self.cancel_event.is_set() or self._aborted.is_set():
                            return
                        yield chunk
                    return
                chunks = accumulator.poll()
            else:
                chunks = accumulator.feed(delta)
            for chunk in chunks:
                if self.cancel_event.is_set() or self._aborted.is_set():
                    return
                yield chunk
        self._discard()
