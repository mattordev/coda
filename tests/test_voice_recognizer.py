import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from runtime.follow_up import FollowUpState
from runtime.messages import InputSource
from runtime.runtime_queue import RuntimeQueue
from utils import voice_recognizer
from utils.voice_recognizer import _queue_runtime_request


class VoiceRecognizerTests(unittest.TestCase):

    def test_queues_voice_runtime_request(self):
        request_queue = RuntimeQueue()

        request = _queue_runtime_request(
            "where is the station",
            request_queue,
        )
        queued_request = request_queue.get(timeout=0.1)

        self.assertIs(queued_request, request)
        self.assertEqual(queued_request.message, "where is the station")
        self.assertEqual(queued_request.source, InputSource.VOICE)
        
    def test_active_follow_up_queues_message_without_wakeword(self):
        request_queue = RuntimeQueue()
        follow_up_state = FollowUpState()
        stop_event = Event()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        microphone = MagicMock()

        follow_up_state.open(duration_seconds=10.0)

        def transcribe_audio(_recognizer, _audio):
            stop_event.set()
            return "connected", "test provider"

        with (
            patch.object(
                voice_recognizer.sr,
                "Recognizer",
                return_value=recognizer,
            ),
            patch.object(
                voice_recognizer,
                "_get_microphone",
                return_value=microphone,
            ),
            patch.object(
                voice_recognizer.stt_service,
                "transcribe_audio",
                side_effect=transcribe_audio,
            ),
            patch.object(
                voice_recognizer.dashboard_state,
                "record_user_message",
            ),
            patch.object(voice_recognizer, "display_message"),
        ):
            voice_recognizer.run(
                ["coda"],
                {},
                stop_event=stop_event,
                request_queue=request_queue,
                follow_up_state=follow_up_state,
            )

        queued_request = request_queue.get(timeout=0.1)

        self.assertIsNotNone(queued_request)
        self.assertEqual(queued_request.message, "connected")
        self.assertEqual(queued_request.source, InputSource.VOICE)
    
    def test_follow_up_stop_phrase_closes_window_without_queueing(self):
        request_queue = RuntimeQueue()
        follow_up_state = FollowUpState()
        stop_event = Event()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        microphone = MagicMock()

        follow_up_state.open(duration_seconds=10.0)

        def transcribe_audio(_recognizer, _audio):
            stop_event.set()
            return "stop", "test provider"

        with (
            patch.object(
                voice_recognizer.sr,
                "Recognizer",
                return_value=recognizer,
            ),
            patch.object(
                voice_recognizer,
                "_get_microphone",
                return_value=microphone,
            ),
            patch.object(
                voice_recognizer.stt_service,
                "transcribe_audio",
                side_effect=transcribe_audio,
            ),
            patch.object(
                voice_recognizer.dashboard_state,
                "record_user_message",
            ),
            patch.object(voice_recognizer, "display_message"),
        ):
            voice_recognizer.run(
                ["coda"],
                {},
                stop_event=stop_event,
                request_queue=request_queue,
                follow_up_state=follow_up_state,
            )

        self.assertFalse(follow_up_state.is_active())
        self.assertIsNone(request_queue.get(timeout=0.01))


if __name__ == "__main__":
    unittest.main()
