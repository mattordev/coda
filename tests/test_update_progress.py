import io
import unittest
from unittest.mock import patch

from colorama import Fore, Style

from utils.update_progress import UpdateProgress


class UpdateProgressTests(unittest.TestCase):
    def test_duration_grades_thresholds_and_resets_colour(self):
        progress = UpdateProgress("Installing", io.StringIO(), budget=100)
        progress.started = 10
        for seconds, colour in ((0, Fore.GREEN), (49.99, Fore.GREEN),
                                (50, Fore.YELLOW), (79.99, Fore.YELLOW),
                                (80, Fore.RED), (150, Fore.RED)):
            with self.subTest(seconds=seconds), \
                    patch("utils.update_progress.time.monotonic", return_value=10 + seconds):
                duration = progress._duration()
                self.assertTrue(duration.startswith(colour + Style.BRIGHT))
                self.assertTrue(duration.endswith(Style.RESET_ALL))

    def test_nonpositive_timing_budget_is_rejected(self):
        for budget in (0, -1):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                UpdateProgress("Installing", io.StringIO(), budget=budget)

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
                progress._frame()
        self.assertFalse(progress.thread.is_alive())
        self.assertIn("\x1b[", stream.getvalue())
        self.assertIn("[####################]", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("\n"))
        self.assertIn(Fore.BLUE, stream.getvalue())
        self.assertIn(f"{Fore.GREEN}{Style.BRIGHT}done{Style.RESET_ALL}", stream.getvalue())
        self.assertNotIn("\x1b[42m", stream.getvalue())

    def test_terminal_failure_status_has_bright_red_text(self):
        stream = io.StringIO()
        with patch.object(stream, "isatty", return_value=True), \
                patch("utils.update_progress.just_fix_windows_console"):
            with self.assertRaises(ValueError):
                with UpdateProgress("Installing", stream) as progress:
                    raise ValueError("fixture")
        self.assertFalse(progress.thread.is_alive())
        self.assertIn(f"{Fore.RED}{Style.BRIGHT}stopped{Style.RESET_ALL}", stream.getvalue())
        self.assertNotIn("\x1b[41m", stream.getvalue())
        self.assertNotIn("[####################]", stream.getvalue())

    def test_estimated_fill_is_monotonic_and_never_full(self):
        progress = UpdateProgress("Installing", io.StringIO(), estimate=10)
        progress.started = 0
        previous = 0
        for seconds, expected in ((0, 0), (5, 9), (10, 18), (20, 18), (100, 19), (10000, 19)):
            with self.subTest(seconds=seconds), \
                    patch("utils.update_progress.time.monotonic", return_value=seconds):
                bar = progress._estimated_bar()
                self.assertEqual(len(bar), 20)
                self.assertEqual(bar.count("#"), expected)
                self.assertGreaterEqual(expected, previous)
                previous = expected

    def test_estimate_must_be_positive(self):
        with self.assertRaises(ValueError):
            UpdateProgress("Installing", io.StringIO(), estimate=0)

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
