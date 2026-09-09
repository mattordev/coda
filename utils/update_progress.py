"""Terminal-only activity feedback; package output stays in the private log."""

import sys
import threading
import time

from colorama import Fore, Style, just_fix_windows_console


class UpdateProgress:
    """Time-estimated ASCII progress; only successful completion fills the bar."""

    def __init__(self, label, stream=None, *, budget=60, estimate=15):
        if budget <= 0:
            raise ValueError("Progress timing budget must be positive")
        if estimate <= 0:
            raise ValueError("Progress estimate must be positive")
        self.label = label
        self.budget = budget
        self.estimate = estimate
        self.filled = 0
        self.stream = sys.stdout if stream is None else stream
        self.interactive = self.stream.isatty()
        self.stop = threading.Event()
        self.thread = None
        self.started = 0

    def _write(self, message):
        try:
            self.stream.write(message)
            self.stream.flush()
        except (OSError, ValueError):
            # A closed console must not interrupt activation or recovery.
            self.stop.set()

    def _elapsed(self):
        seconds = max(0.0, time.monotonic() - self.started)
        return "<0.01s" if seconds < 0.01 else f"{seconds:.2f}s"

    def _duration(self):
        seconds = max(0.0, time.monotonic() - self.started)
        colour = Fore.GREEN if seconds < self.budget * 0.5 else (
            Fore.YELLOW if seconds < self.budget * 0.8 else Fore.RED
        )
        elapsed = "<0.01s" if seconds < 0.01 else f"{seconds:.2f}s"
        return f"{colour}{Style.BRIGHT}{elapsed}{Style.RESET_ALL}"

    def _estimated_bar(self):
        elapsed = max(0.0, time.monotonic() - self.started)
        ratio = elapsed / self.estimate
        # Reach 90% at the estimate, then approach (but never reach) full.
        fraction = 0.9 * ratio if ratio <= 1 else 0.9 + 0.09 * (1 - 1 / ratio)
        self.filled = max(self.filled, min(19, int(fraction * 20)))
        return "#" * self.filled + "-" * (20 - self.filled)

    def _frame(self):
        bar = self._estimated_bar()
        self._write(
            f"\r{Fore.BLUE}{Style.BRIGHT}[{bar}]{Style.RESET_ALL} "
            f"{self.label} (estimated; {self._duration()})"
        )

    def _animate(self):
        while not self.stop.wait(0.2):
            self._frame()

    def __enter__(self):
        self.started = time.monotonic()
        if self.interactive:
            just_fix_windows_console()
            self._frame()
            self.thread = threading.Thread(target=self._animate, daemon=True)
            self.thread.start()
        else:
            self._write(f"[UPDATE] {self.label}...\n")
        return self

    def __exit__(self, error_type, error, traceback):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        status = "done" if error_type is None else "stopped"
        if self.interactive:
            status_colour = Fore.GREEN if error_type is None else Fore.RED
            bar = "#" * 20 if error_type is None else "#" * self.filled + "-" * (20 - self.filled)
            self._write(
                f"\r{Fore.BLUE}{Style.BRIGHT}[{bar}]{Style.RESET_ALL} "
                f"{self.label} (estimated; {self._duration()}) "
                f"{status_colour}{Style.BRIGHT}{status}{Style.RESET_ALL}\n"
            )
        else:
            self._write(f"[UPDATE] {self.label}: {status}\n")
