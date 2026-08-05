import os
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

        with (
            patch.object(
                voice_recognizer.runtime_state,
                "is_debug_enabled",
                return_value=False,
            ),
            patch("builtins.print") as print_output,
        ):
            request = _queue_runtime_request(
                "where is the station",
                request_queue,
            )

        queued_request = request_queue.get(timeout=0.1)

        self.assertIs(queued_request, request)
        self.assertEqual(
            queued_request.message,
            "where is the station",
        )
        self.assertEqual(
            queued_request.source,
            InputSource.VOICE,
        )
        print_output.assert_called_once_with(
            "[RUNTIME] Request accepted",
            flush=True,
        )

    def test_debug_prints_request_details_when_queueing(self):
        request_queue = RuntimeQueue()

        with (
            patch.object(
                voice_recognizer.runtime_state,
                "is_debug_enabled",
                return_value=True,
            ),
            patch("builtins.print") as print_output,
        ):
            request = _queue_runtime_request(
                "where is the station",
                request_queue,
            )

        print_output.assert_called_once_with(
            f"[RUNTIME] Accepted voice request "
            f"{request.request_id[:8]}",
            flush=True,
        )
        
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

    def test_empty_microphone_index_allows_name_selection(self):
        microphone_factory = MagicMock()
        microphone_factory.list_microphone_names.return_value = [
            "System default",
            "Microphone (Yeti Orb)",
        ]

        with (
            patch.dict(
                os.environ,
                {
                    "CODA_LIST_MICS_ON_START": "0",
                    "CODA_MIC_INDEX": "",
                    "CODA_MIC_NAME": "Yeti Orb",
                },
            ),
            patch.object(
                voice_recognizer.sr,
                "Microphone",
                microphone_factory,
            ),
        ):
            voice_recognizer._get_microphone()

        microphone_factory.assert_called_once_with(device_index=1)

    def test_follow_up_opened_during_transcription_queues_message(self):
        request_queue = RuntimeQueue()
        follow_up_state = FollowUpState()
        stop_event = Event()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        microphone = MagicMock()

        def transcribe_audio(_recognizer, _audio):
            follow_up_state.open(duration_seconds=10.0)
            stop_event.set()
            return "what about sunsets", "test provider"

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
        self.assertEqual(queued_request.message, "what about sunsets")
        self.assertEqual(queued_request.source, InputSource.VOICE)


if __name__ == "__main__":
    unittest.main()
