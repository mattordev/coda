"""Optional terminal preview; final history/dashboard remain complete responses."""
from threading import Lock
from utils.console import console_output


def _write(text):
    print(text, end='', flush=True)


class IncrementalDisplay:
    def __init__(self, cancel_event, *, write=_write, console=console_output):
        self.cancel_event = cancel_event
        self._write = write
        self._console = console
        self._lock = Lock()
        self._closed = False
        self.started = False

    def feed(self, text):
        with self._lock:
            if self._closed or self.cancel_event.is_set():
                raise InterruptedError('Request cancelled.')
            # Escape/control bytes must never reach a terminal, even across
            # delta boundaries. Visible Unicode and normal line breaks survive.
            text = ''.join(char for char in text
                           if char in '\n\t' or (ord(char) >= 32 and not 127 <= ord(char) <= 159))
            if not text or (not self.started and not text.strip()):
                return
            if not self.started:
                self.started = True
                text = 'CODA: ' + text
            self._console.write_response(self, text, self._write)

    def finish(self, success):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.started:
                try:
                    self._console.write_response(
                        self, '\n' if success and not self.cancel_event.is_set()
                        else '\n[Response incomplete.]\n', self._write)
                finally:
                    self._console.end_response(self)
