"""Serialize application messages around a streamed terminal answer."""
import builtins
from collections import deque
import sys
from threading import RLock


class ConsoleOutput:
    """Defer complete log lines without blocking capture or cancellation."""

    def __init__(self, limit=100):
        self._lock = RLock()
        self._owner = None
        self._pending = deque(maxlen=limit)
        self._dropped = 0

    def print_line(self, *args, **kwargs):
        with self._lock:
            target = kwargs.get('file')
            if self._owner is None or (
                target is not None and target is not sys.stdout and target is not sys.stderr
            ):
                builtins.print(*args, **kwargs)
                return
            # Store rendered text, not references to arbitrary application objects.
            sep = kwargs.get('sep')
            end = kwargs.get('end')
            text = (' ' if sep is None else sep).join(str(arg) for arg in args)
            text += '\n' if end is None else end
            if len(text) > 8192:
                text = text[:8177] + ' [truncated]\n'
            if len(self._pending) == self._pending.maxlen:
                self._dropped += 1
            self._pending.append((text, target))

    def write_response(self, owner, text, write):
        with self._lock:
            if self._owner is not None and self._owner is not owner:
                raise RuntimeError('Another terminal response is active.')
            self._owner = owner
            try:
                write(text)
            except BaseException:
                self.end_response(owner)
                raise

    def end_response(self, owner):
        with self._lock:
            if self._owner is not owner:
                return
            self._owner = None
            pending, self._pending = self._pending, deque(maxlen=self._pending.maxlen)
            dropped, self._dropped = self._dropped, 0
            if dropped:
                builtins.print(f'[CONSOLE] {dropped} older background messages omitted.', flush=True)
            for text, target in pending:
                builtins.print(text, end='', file=target, flush=True)


console_output = ConsoleOutput()
print_line = console_output.print_line


def configure_stdout_encoding():
    """Keep LLM text readable when the process runs in Windows PowerShell."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
