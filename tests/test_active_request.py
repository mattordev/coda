import unittest

from runtime.active_request import ActiveRequestState
from runtime.messages import InputSource, RuntimeRequest


class ActiveRequestStateTests(unittest.TestCase):

    def test_activate_tracks_request_id(self):
        state = ActiveRequestState()
        request = RuntimeRequest(
            message="first request",
            source=InputSource.VOICE,
        )

        state.activate(request)

        self.assertEqual(
            state.active_request_id(),
            request.request_id,
        )

    def test_cancel_active_sets_request_event(self):
        state = ActiveRequestState()
        request = RuntimeRequest(
            message="cancel me",
            source=InputSource.VOICE,
        )
        state.activate(request)

        cancelled_id = state.cancel_active()

        self.assertEqual(cancelled_id, request.request_id)
        self.assertTrue(request.cancel_event.is_set())

    def test_cancel_active_is_safe_without_request(self):
        state = ActiveRequestState()

        self.assertIsNone(state.cancel_active())

    def test_stale_clear_does_not_clear_newer_request(self):
        state = ActiveRequestState()
        first = RuntimeRequest(
            message="first",
            source=InputSource.VOICE,
        )
        second = RuntimeRequest(
            message="second",
            source=InputSource.VOICE,
        )

        state.activate(first)
        state.activate(second)

        cleared = state.clear(first.request_id)

        self.assertFalse(cleared)
        self.assertEqual(
            state.active_request_id(),
            second.request_id,
        )


if __name__ == "__main__":
    unittest.main()
