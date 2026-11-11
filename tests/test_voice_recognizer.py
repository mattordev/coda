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
        overlapped_capture=speech_playing,
    )


class VoiceRecognizerTests(unittest.TestCase):
    def test_stop_matching_requires_complete_wakeword_and_final_exact_action(self):
        for text, expected in (
            ('Coda stop.', True),
            ('CODA was speaking. Coda, cancel!', True),
            ('Please stop.', False),
            ('decoda stop', False),
            ('Coda stop searching and explain it', False),
            ('Coda explain the stop command', False),
        ):
            with self.subTest(text=text):
                self.assertEqual(voice_recognizer._has_wakeword_stop(text, ['coda']), expected)
        self.assertFalse(voice_recognizer._has_wakeword_stop('stop', ['', ' ']))

    def test_stop_after_earlier_wakeword_cancels_during_playback(self):
        stop, requests, cancel = Event(), RuntimeQueue(), MagicMock()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        def transcribe(*_args):
            stop.set()
            return 'Coda is still speaking. Coda stop.', 'fixture'
        with patch.object(voice_recognizer.sr, 'Recognizer', return_value=recognizer), \
             patch.object(voice_recognizer, '_get_microphone', return_value=MagicMock()), \
             patch.object(voice_recognizer, 'listen_for_phrase',
                          return_value=_captured_phrase(speech_playing=True)), \
             patch.object(voice_recognizer.stt_service, 'transcribe_audio', side_effect=transcribe), \
             patch.object(voice_recognizer.dashboard_state, 'record_user_message'), \
             patch('builtins.print'):
            voice_recognizer.run(['coda'], {}, stop_event=stop, request_queue=requests,
                                 cancel_active_request=cancel, is_speech_playing=lambda: True)
        cancel.assert_called_once_with()
        self.assertIsNone(requests.get(timeout=0.01))

    def test_wakeword_stop_is_checked_when_playback_ends_before_transcription(self):
        stop = Event()
        requests = RuntimeQueue()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        cancel = MagicMock(return_value='fixture-request')
        follow_up = FollowUpState()
        follow_up.open(10)
        captures = 0
        def capture(*_args, **kwargs):
            nonlocal captures
            captures += 1
            if captures > 1:
                return None
            # A user can finish saying stop at the same time playback ends.
            # Capture must not truncate/discard that phrase as presumed echo.
            return CapturedPhrase(object(), VoiceCaptureState(False, True),
                                  overlapped_capture=True,
                                  playback_ended=kwargs['end_on_playback_end'])
        def transcribe(*_args):
            stop.set()
            return 'Coda stop.', 'fixture'
        with patch.object(voice_recognizer.sr, 'Recognizer', return_value=recognizer), \
             patch.object(voice_recognizer, '_get_microphone', return_value=MagicMock()), \
             patch.object(voice_recognizer, 'listen_for_phrase', side_effect=capture), \
             patch.object(voice_recognizer.stt_service, 'transcribe_audio', side_effect=transcribe) as stt, \
             patch.object(voice_recognizer.dashboard_state, 'record_user_message'), \
             patch('builtins.print'):
            voice_recognizer.run(['coda'], {}, stop_event=stop, request_queue=requests,
                                 follow_up_state=follow_up, cancel_active_request=cancel,
                                 is_speech_playing=lambda: False)
        stt.assert_called_once()
        cancel.assert_called_once_with()
        self.assertFalse(follow_up.is_active())
        self.assertIsNone(requests.get(timeout=0.01))

    def test_finished_playback_echo_skips_stt_and_preserves_fresh_follow_up(self):
        follow_up, stop = FollowUpState(), Event()
        requests = RuntimeQueue()
        calls = 0
        def capture(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                follow_up.open(10)
                return CapturedPhrase(object(), VoiceCaptureState(False, True),
                                      overlapped_capture=True, playback_ended=True)
            return _captured_phrase(follow_up_active=True)
        def transcribe(*_args):
            stop.set()
            return 'list the docs folder', 'fixture'
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        with patch.object(voice_recognizer.sr, 'Recognizer', return_value=recognizer), \
             patch.object(voice_recognizer, '_get_microphone', return_value=MagicMock()), \
             patch.object(voice_recognizer, 'listen_for_phrase', side_effect=capture), \
             patch.object(voice_recognizer.stt_service, 'transcribe_audio', side_effect=transcribe) as stt, \
             patch.object(voice_recognizer.dashboard_state, 'record_user_message'), \
             patch('builtins.print'):
            voice_recognizer.run(['coda'], {}, stop_event=stop, request_queue=requests,
                                 follow_up_state=follow_up, is_speech_playing=lambda: False)
        stt.assert_called_once()
        self.assertEqual(requests.get(timeout=0.1).message, 'list the docs folder')

    def test_follow_up_message_updates_inside_idle_capture_without_a_timeout(self):
        follow_up = FollowUpState()
        recognizer = MagicMock()
        recognizer.energy_threshold = 100
        with patch.object(voice_recognizer.sr, 'Recognizer', return_value=recognizer), \
             patch.object(voice_recognizer, '_get_microphone', return_value=MagicMock()), \
             patch('builtins.print') as printed:
            def capture(_recognizer, _source, state, **kwargs):
                follow_up.open(10)
                kwargs['on_waiting'](state())
                printed.assert_any_call('Waiting for follow-up', flush=True)
                return None
            with patch.object(voice_recognizer, 'listen_for_phrase', side_effect=capture):
                voice_recognizer.run(['coda'], {}, follow_up_state=follow_up)

    def test_listener_announces_state_changes_without_repeating_timeouts(self):
        recognizer, microphone = MagicMock(), MagicMock()
        recognizer.energy_threshold = 100
        follow_up = FollowUpState()
        attempts = 0
        def capture(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                follow_up.open(duration_seconds=10)
            if attempts < 3:
                raise voice_recognizer.sr.WaitTimeoutError()
            return None
        with patch.object(voice_recognizer.sr, 'Recognizer', return_value=recognizer), \
             patch.object(voice_recognizer, '_get_microphone', return_value=microphone), \
             patch.object(voice_recognizer, 'listen_for_phrase', side_effect=capture), \
             patch('builtins.print') as printed:
            voice_recognizer.run(['coda'], {}, follow_up_state=follow_up)
        lines = [call.args[0] for call in printed.call_args_list]
        self.assertEqual(lines.count('Listener ready for voice input'), 1)
        self.assertEqual(lines.count('Waiting for follow-up'), 1)

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
            return "Connected to API", "test provider"

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
        self.assertEqual(queued_request.message, "Connected to API")
        self.assertEqual(queued_request.source, InputSource.VOICE)
        record_user_message.assert_called_once_with(
            "Connected to API",
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

        def capture_phrase(_recognizer, _source, capture_state, **kwargs):
            state = capture_state()
            return CapturedPhrase(
                audio=object(),
                started_with=state,
                overlapped_capture=kwargs["blocks_capture"](state),
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
            "[VOICE] Audio overlapped CODA speech. Ignoring phrase."
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
