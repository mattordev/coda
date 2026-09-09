import io
import unittest
from unittest.mock import patch

from colorama import Fore, Style

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
        self.assertIn(f"{Fore.LIGHTGREEN_EX}{Style.BRIGHT}done{Style.RESET_ALL}", stream.getvalue())

    def test_terminal_failure_status_is_bright_red(self):
        stream = io.StringIO()
        with patch.object(stream, "isatty", return_value=True), \
                patch("utils.update_progress.just_fix_windows_console"):
            with self.assertRaises(ValueError):
                with UpdateProgress("Installing", stream) as progress:
                    raise ValueError("fixture")
        self.assertFalse(progress.thread.is_alive())
        self.assertIn(f"{Fore.LIGHTRED_EX}{Style.BRIGHT}stopped{Style.RESET_ALL}", stream.getvalue())

    def test_closed_output_does_not_mask_operation(self):
        stream = io.StringIO()
        progress = UpdateProgress("Installing", stream)
        stream.close()
        with progress:
            pass
