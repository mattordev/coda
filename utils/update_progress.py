"""Terminal-only activity feedback; package output stays in the private log."""

import sys
import threading
import time

from colorama import Fore, Style, just_fix_windows_console


class UpdateProgress:
    """An indeterminate ASCII bar, not an estimated download percentage."""

    def __init__(self, label, stream=None):
        self.label = label
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

    def _frame(self, tick):
        position = tick % 20
        bar = " " * position + "#" + " " * (19 - position)
        elapsed = int(time.monotonic() - self.started)
        self._write(f"\r{Fore.CYAN}[{bar}] {self.label} ({elapsed}s){Style.RESET_ALL}")

    def _animate(self):
        tick = 1
        while not self.stop.wait(0.2):
            self._frame(tick)
            tick += 1

    def __enter__(self):
        self.started = time.monotonic()
        if self.interactive:
            just_fix_windows_console()
            self._frame(0)
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
            colour = Fore.GREEN if error_type is None else Fore.RED
            status_colour = Fore.LIGHTGREEN_EX if error_type is None else Fore.LIGHTRED_EX
            elapsed = int(time.monotonic() - self.started)
            self._write(
                f"\r{colour}[{'#' * 20}] {self.label} ({elapsed}s) "
                f"{status_colour}{Style.BRIGHT}{status}{Style.RESET_ALL}\n"
            )
        else:
            self._write(f"[UPDATE] {self.label}: {status}\n")
