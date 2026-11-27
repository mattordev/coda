"""Spoken feedback appears when playback starts, without duplicating answers."""

from copy import deepcopy
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

import coda_runtime
from runtime.messages import SpeechTask, WorkerEventType
from runtime.speech_playback import SpeechPlaybackController
from runtime.speech_worker import SpeechTaskProcessor
from utils import dashboard_state, llm_service


class FeedbackProvider:
    name = 'fixture'

    def __init__(self, before_audio=None, fail=False):
        self.before_audio = before_audio or (lambda: None)
        self.fail = fail

    def is_available(self):
        return True

    def create_session(self, _text, *, on_playback_start=None):
        session = Mock()

        def play(cancel_event):
            self.before_audio()
            if self.fail:
                raise RuntimeError('No audio generated')
            if cancel_event.is_set():
                return False
            if on_playback_start is not None:
                on_playback_start()
                on_playback_start()  # Repeated audio frames cannot duplicate chat.
            return True

        session.play.side_effect = play
        return session


class SpokenFeedbackDisplayTests(unittest.TestCase):
    def task(self, *, feedback=True):
        return SpeechTask('request', "I'm searching the web now.", Event(), is_acknowledgement=feedback)

    def processor(self, providers, publish):
        return SpeechTaskProcessor(lambda: providers, SpeechPlaybackController(),
                                   segment_text=lambda text: [text, text], on_feedback=publish)

    def test_feedback_publishes_at_first_playback_once_across_frames_and_segments(self):
        publish = Mock()
        before = Mock(side_effect=lambda: publish.assert_not_called())
        provider = FeedbackProvider()
        # Only the first segment must precede publication.
        provider.before_audio = lambda: before() if before.call_count == 0 else None
        task = self.task()
        result = self.processor([provider], publish).process(task)
        self.assertEqual(result.event_type, WorkerEventType.COMPLETED)
        publish.assert_called_once_with(task)

    def test_cancelled_unavailable_and_failed_audio_do_not_publish(self):
        for mode in ('queued_cancel', 'preparation_cancel', 'unavailable', 'failed'):
            with self.subTest(mode=mode):
                publish, task = Mock(), self.task()
                provider = FeedbackProvider()
                if mode == 'queued_cancel':
                    task.cancel_event.set()
                elif mode == 'preparation_cancel':
                    provider.before_audio = task.cancel_event.set
                elif mode == 'failed':
                    provider.fail = True
                self.processor([] if mode == 'unavailable' else [provider], publish).process(task)
                publish.assert_not_called()

    def test_provider_fallback_and_display_errors_do_not_break_speech(self):
        publish = Mock(side_effect=OSError('dashboard locked'))
        task = self.task()
        result = self.processor([FeedbackProvider(fail=True), FeedbackProvider()], publish).process(task)
        self.assertEqual(result.event_type, WorkerEventType.COMPLETED)
        publish.assert_called_once_with(task)

    def test_final_answers_are_not_published_twice(self):
        publish = Mock()
        result = self.processor([FeedbackProvider()], publish).process(self.task(feedback=False))
        self.assertEqual(result.event_type, WorkerEventType.COMPLETED)
        publish.assert_not_called()

    def test_console_and_dashboard_show_feedback_without_changing_model_history(self):
        with tempfile.TemporaryDirectory() as directory:
            history = deepcopy(llm_service.conversation_log)
            with (patch.object(dashboard_state, '_STATE_FILE', Path(directory) / 'state.json'),
                  patch('builtins.print') as output):
                task = self.task()
                processor = self.processor([FeedbackProvider()], coda_runtime._display_spoken_feedback)
                self.assertEqual(processor.process(task).event_type, WorkerEventType.COMPLETED)
                visible = dashboard_state.snapshot()
            output.assert_any_call(f'CODA: {task.text}', flush=True)
            self.assertEqual([message['text'] for message in visible['ai_messages']], [task.text])
            self.assertEqual(visible['ai_messages'][0]['source'], 'voice_feedback')
            self.assertEqual(llm_service.conversation_log, history)
