import threading
import unittest
from unittest import mock

import coda_runtime
from runtime.active_request import ActiveRequestState
from runtime.messages import InputSource, RuntimeRequest


class RuntimeShutdownTests(unittest.TestCase):
    def test_shutdown_cancels_active_request_and_stops_every_worker(self):
        shutdown_started = threading.Event()
        active_request_state = ActiveRequestState()
        request = RuntimeRequest(
            message="long-running request",
            source=InputSource.VOICE,
        )
        active_request_state.activate(request)

        with mock.patch.object(
            coda_runtime,
            "shutdown_started",
            shutdown_started,
        ), mock.patch.object(
            coda_runtime,
            "active_request_state",
            active_request_state,
        ), mock.patch.object(
            coda_runtime.follow_up_state,
            "close",
        ) as close_follow_up, mock.patch.object(
            coda_runtime,
            "stop_voice_thread",
        ) as stop_voice, mock.patch.object(
            coda_runtime,
            "stop_execution_thread",
        ) as stop_execution, mock.patch.object(
            coda_runtime.speech,
            "configure_speech_submitter",
        ) as configure_speech, mock.patch.object(
            coda_runtime,
            "stop_speech_thread",
        ) as stop_speech, mock.patch.object(
            coda_runtime,
            "stop_event_thread",
        ) as stop_events, mock.patch.object(
            coda_runtime,
            "stop_heartbeat_thread",
        ) as stop_heartbeat:
            stopped = coda_runtime.shutdown_runtime(timeout_seconds=1.5)

        self.assertTrue(stopped)
        self.assertTrue(request.cancel_event.is_set())
        close_follow_up.assert_called_once_with()
        stop_voice.assert_called_once_with(timeout_seconds=1.5)
        stop_execution.assert_called_once_with(timeout_seconds=1.5)
        configure_speech.assert_called_once_with(None)
        stop_speech.assert_called_once_with(timeout_seconds=1.5)
        stop_events.assert_called_once_with(timeout_seconds=1.5)
        stop_heartbeat.assert_called_once_with(timeout_seconds=1.5)

    def test_shutdown_is_idempotent(self):
        shutdown_started = threading.Event()

        with mock.patch.object(
            coda_runtime,
            "shutdown_started",
            shutdown_started,
        ), mock.patch.object(
            coda_runtime,
            "stop_voice_thread",
        ) as stop_voice, mock.patch.object(
            coda_runtime.active_request_state,
            "cancel_active",
        ), mock.patch.object(
            coda_runtime,
            "stop_execution_thread",
        ), mock.patch.object(
            coda_runtime.speech,
            "configure_speech_submitter",
        ), mock.patch.object(
            coda_runtime,
            "stop_speech_thread",
        ), mock.patch.object(
            coda_runtime,
            "stop_event_thread",
        ), mock.patch.object(
            coda_runtime,
            "stop_heartbeat_thread",
        ):
            first_result = coda_runtime.shutdown_runtime()
            second_result = coda_runtime.shutdown_runtime()

        self.assertTrue(first_result)
        self.assertFalse(second_result)
        stop_voice.assert_called_once_with(timeout_seconds=4.0)

    def test_main_shuts_down_cleanly_after_keyboard_interrupt(self):
        shutdown_started = threading.Event()

        with mock.patch.object(
            coda_runtime,
            "shutdown_started",
            shutdown_started,
        ), mock.patch.object(
            coda_runtime,
            "_run_runtime",
            side_effect=KeyboardInterrupt,
        ), mock.patch.object(
            coda_runtime,
            "shutdown_runtime",
        ) as shutdown, mock.patch("builtins.print") as print_output:
            coda_runtime.main()

        shutdown.assert_called_once_with()
        print_output.assert_called_once_with("Exiting C.O.D.A")


if __name__ == "__main__":
    unittest.main()
