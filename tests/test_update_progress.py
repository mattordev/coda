import io
import unittest
from unittest.mock import patch

from colorama import Back, Fore, Style

from utils.update_progress import UpdateProgress


class UpdateProgressTests(unittest.TestCase):
    def test_redirected_output_has_no_animation_or_colour(self):
        stream = io.StringIO()
        with UpdateProgress("Installing", stream) as progress:
            self.assertIsNone(progress.thread)
        self.assertEqual(stream.getvalue(), "[UPDATE] Installing...\n[UPDATE] Installing: done\n")

    def test_failure_is_reported_and_propagated(self):
        stream = io.StringIO()
        with self.assertRaisesRegex(ValueError, "fixture"):
            with UpdateProgress("Installing", stream):
                raise ValueError("fixture")
        self.assertIn("Installing: stopped", stream.getvalue())

    def test_terminal_bar_is_coloured_and_thread_stops(self):
        stream = io.StringIO()
        with patch.object(stream, "isatty", return_value=True), \
                patch("utils.update_progress.just_fix_windows_console"):
            with UpdateProgress("Installing", stream) as progress:
                progress._frame(4)
        self.assertFalse(progress.thread.is_alive())
        self.assertIn("\x1b[", stream.getvalue())
        self.assertIn("[    #", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("\n"))
        self.assertIn(Fore.YELLOW, stream.getvalue())
        self.assertIn(f"{Back.GREEN}{Fore.BLACK}{Style.NORMAL} done {Style.RESET_ALL}", stream.getvalue())

    def test_terminal_failure_status_has_red_background(self):
        stream = io.StringIO()
        with patch.object(stream, "isatty", return_value=True), \
                patch("utils.update_progress.just_fix_windows_console"):
            with self.assertRaises(ValueError):
                with UpdateProgress("Installing", stream) as progress:
                    raise ValueError("fixture")
        self.assertFalse(progress.thread.is_alive())
        self.assertIn(f"{Back.RED}{Fore.BLACK}{Style.NORMAL} stopped {Style.RESET_ALL}", stream.getvalue())

    def test_elapsed_preserves_fractional_seconds(self):
        progress = UpdateProgress("Downloading", io.StringIO())
        progress.started = 10
        for now, expected in ((10, "<0.01s"), (10.004, "<0.01s"), (10.25, "0.25s"), (72.5, "62.50s")):
            with self.subTest(now=now), patch("utils.update_progress.time.monotonic", return_value=now):
                self.assertEqual(progress._elapsed(), expected)

    def test_closed_output_does_not_mask_operation(self):
        stream = io.StringIO()
        progress = UpdateProgress("Installing", stream)
        stream.close()
        with progress:
            pass
