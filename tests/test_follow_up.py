import unittest
from unittest.mock import patch

from runtime.follow_up import FollowUpState


class FollowUpStateTests(unittest.TestCase):

    def test_window_remains_active_until_deadline(self):
        state = FollowUpState()

        with patch("runtime.follow_up.time.monotonic", return_value=100.0):
            state.open(duration_seconds=10.0)

        with patch("runtime.follow_up.time.monotonic", return_value=109.9):
            self.assertTrue(state.is_active())

        with patch("runtime.follow_up.time.monotonic", return_value=110.0):
            self.assertFalse(state.is_active())

    def test_close_ends_active_window(self):
        state = FollowUpState()

        with patch("runtime.follow_up.time.monotonic", return_value=100.0):
            state.open(duration_seconds=10.0)
            state.close()
            self.assertFalse(state.is_active())


if __name__ == "__main__":
    unittest.main()