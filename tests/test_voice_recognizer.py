import os
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from runtime.follow_up import FollowUpState
from runtime.messages import InputSource
from runtime.runtime_queue import RuntimeQueue
from utils import voice_recognizer
from utils.phrase_listener import CapturedPhrase
from utils.voice_recognizer import VoiceCaptureState, _queue_runtime_request


def _captured_phrase(
    *,
    follow_up_active=False,
    speech_playing=False,
):
    return CapturedPhrase(
        audio=object(),
        started_with=VoiceCaptureState(
            follow_up_active=follow_up_active,
            speech_playing=speech_playing,
        ),
    )


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
        
    def test_follow_up_active_at_capture_queues_after_window_closes(self):
        request_queue = RuntimeQueue()
        follow_up_state = FollowUpState()
        stop_event = Event()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        microphone = MagicMock()

        follow_up_state.open(duration_seconds=10.0)

        def transcribe_audio(_recognizer, _audio):
            follow_up_state.close()
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
                voice_recognizer,
                "listen_for_phrase",
                return_value=_captured_phrase(
                    follow_up_active=True,
                ),
            ),
            patch.object(
                voice_recognizer.stt_service,
                "transcribe_audio",
                side_effect=transcribe_audio,
            ),
            patch.object(
                voice_recognizer.dashboard_state,
                "record_user_message",
            ) as record_user_message,
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
        record_user_message.assert_called_once_with(
            "connected",
            source="test provider",
            tags=["follow_up"],
        )
    
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
                voice_recognizer,
                "listen_for_phrase",
                return_value=_captured_phrase(
                    follow_up_active=True,
                ),
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

    def test_follow_up_opened_after_capture_does_not_queue_message(self):
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
                voice_recognizer,
                "listen_for_phrase",
                return_value=_captured_phrase(),
            ),
            patch.object(
                voice_recognizer.stt_service,
                "transcribe_audio",
                side_effect=transcribe_audio,
            ),
            patch.object(
                voice_recognizer.dashboard_state,
                "record_user_message",
            ) as record_user_message,
            patch.object(voice_recognizer, "display_message"),
        ):
            voice_recognizer.run(
                ["coda"],
                {},
                stop_event=stop_event,
                request_queue=request_queue,
                follow_up_state=follow_up_state,
            )

        self.assertIsNone(request_queue.get(timeout=0.01))
        self.assertTrue(follow_up_state.is_active())
        record_user_message.assert_called_once_with(
            "what about sunsets",
            source="test provider",
            tags=["pre_follow_up_audio"],
        )
        
    def test_wakeword_stop_cancels_without_queueing(self):
        request_queue = RuntimeQueue()
        follow_up_state = FollowUpState()
        stop_event = Event()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        microphone = MagicMock()
        cancel_active_request = MagicMock(
            return_value="request-id",
        )

        def transcribe_audio(_recognizer, _audio):
            stop_event.set()
            return "coda stop", "test provider"

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
                voice_recognizer,
                "listen_for_phrase",
                return_value=_captured_phrase(),
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
                cancel_active_request=cancel_active_request,
            )

        cancel_active_request.assert_called_once_with()
        self.assertIsNone(
            request_queue.get(timeout=0.01)
        )

    def test_playback_echo_is_ignored_after_playback_ends(self):
        request_queue = RuntimeQueue()
        follow_up_state = FollowUpState()
        stop_event = Event()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        microphone = MagicMock()
        playback_active = True

        def capture_phrase(_recognizer, _source, capture_state, **_kwargs):
            return CapturedPhrase(
                audio=object(),
                started_with=capture_state(),
            )

        def transcribe_audio(_recognizer, _audio):
            nonlocal playback_active
            playback_active = False
            stop_event.set()
            return "coda system status", "test provider"

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
                voice_recognizer,
                "listen_for_phrase",
                side_effect=capture_phrase,
            ),
            patch.object(
                voice_recognizer.stt_service,
                "transcribe_audio",
                side_effect=transcribe_audio,
            ),
            patch.object(
                voice_recognizer.dashboard_state,
                "record_user_message",
            ) as record_user_message,
            patch.object(voice_recognizer, "display_message"),
            patch("builtins.print") as print_output,
        ):
            voice_recognizer.run(
                ["coda"],
                {},
                stop_event=stop_event,
                request_queue=request_queue,
                follow_up_state=follow_up_state,
                is_speech_playing=lambda: playback_active,
            )

        self.assertIsNone(request_queue.get(timeout=0.01))
        record_user_message.assert_called_once_with(
            "coda system status",
            source="test provider",
            tags=["wakeword_detected", "assistant_playback"],
        )
        print_output.assert_any_call(
            "[VOICE] Audio began during CODA speech. Ignoring phrase."
        )

    def test_wakeword_stop_cancels_during_playback(self):
        request_queue = RuntimeQueue()
        stop_event = Event()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        microphone = MagicMock()
        cancel_active_request = MagicMock(return_value="request-id")

        def transcribe_audio(_recognizer, _audio):
            stop_event.set()
            return "coda stop", "test provider"

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
                voice_recognizer,
                "listen_for_phrase",
                return_value=_captured_phrase(speech_playing=True),
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
                cancel_active_request=cancel_active_request,
                is_speech_playing=lambda: True,
            )

        cancel_active_request.assert_called_once_with()
        self.assertIsNone(request_queue.get(timeout=0.01))

    def test_playback_echo_does_not_close_new_follow_up(self):
        request_queue = RuntimeQueue()
        follow_up_state = FollowUpState()
        stop_event = Event()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        microphone = MagicMock()

        def transcribe_audio(_recognizer, _audio):
            follow_up_state.open(duration_seconds=10.0)
            stop_event.set()
            return "coda system status", "test provider"

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
                voice_recognizer,
                "listen_for_phrase",
                return_value=_captured_phrase(speech_playing=True),
            ),
            patch.object(
                voice_recognizer.stt_service,
                "transcribe_audio",
                side_effect=transcribe_audio,
            ),
            patch.object(
                voice_recognizer.dashboard_state,
                "record_user_message",
            ) as record_user_message,
            patch.object(voice_recognizer, "display_message"),
        ):
            voice_recognizer.run(
                ["coda"],
                {},
                stop_event=stop_event,
                request_queue=request_queue,
                follow_up_state=follow_up_state,
                is_speech_playing=lambda: True,
            )

        self.assertIsNone(request_queue.get(timeout=0.01))
        self.assertTrue(follow_up_state.is_active())
        record_user_message.assert_called_once_with(
            "coda system status",
            source="test provider",
            tags=["wakeword_detected", "assistant_playback"],
        )


if __name__ == "__main__":
    unittest.main()
