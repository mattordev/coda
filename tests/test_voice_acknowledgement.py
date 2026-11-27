"""Acknowledgements fill silence without changing the answer's lifecycle."""

from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import coda_runtime
from ai.request_context import current_request
from runtime.active_request import ActiveRequestState
from runtime.messages import InputSource, RuntimeRequest, SpeechTask, WorkerEvent, WorkerEventType, WorkerName
from runtime.runtime_queue import RuntimeQueues
from runtime.speech_playback import SpeechPlaybackController
from runtime.speech_worker import SpeechTaskProcessor, SpeechWorker
from runtime.voice_acknowledgement import PHRASES, VoiceAcknowledgement
from ai.tools.progress import ToolFeedback


class VoiceAcknowledgementTests(unittest.TestCase):
    def test_complete_voice_tool_flow_speaks_ack_action_progress_then_answer(self):
        from ai.intents.models import Intent, IntentRequest
        from ai.tools.external import ExternalToolAdapter
        from ai.tools.models import ToolResult
        from ai.tools.progress import report_tool

        queues, shutdown, speech_shutdown = RuntimeQueues(), Event(), Event()
        request = RuntimeRequest('Search for something', InputSource.VOICE)
        spoken = []
        ack_heard, action_heard, progress_heard, answer_heard = Event(), Event(), Event(), Event()
        provider, controller = Mock(), Mock()
        provider.is_available.return_value = True

        def play(_provider, text, cancel_event, **_kwargs):
            if cancel_event.is_set():
                return False
            spoken.append(text)
            if text == PHRASES[0]:
                ack_heard.set()
            elif text == "I'm searching the web now.":
                action_heard.set()
            elif 'snippets' in text:
                progress_heard.set()
            elif text == 'Here is the answer.':
                answer_heard.set()
            return True

        controller.play.side_effect = play
        processor = SpeechTaskProcessor(lambda: [provider], controller, segment_text=lambda text: [text])
        speech_worker = SpeechWorker(queues.speech, queues.events, processor, speech_shutdown)

        def factory(*args):
            return VoiceAcknowledgement(*args, delay=0, choose=lambda phrases: phrases[0])

        def run(*_args, **_kwargs):
            self.assertTrue(ack_heard.wait(2))
            intent = Intent('mcp.search.search_web', 'Search', source='mcp',
                            input_schema={'type': 'object', 'properties': {}})

            def invoke(_arguments):
                self.assertTrue(action_heard.wait(2))
                return ToolResult(True, {'kind': 'web_search', 'results': [{'snippet': 'Observed.'}]})

            result = ExternalToolAdapter(intent, invoke).execute(IntentRequest(intent, 'Search', 1, arguments={}))
            self.assertTrue(result.success)
            report_tool('progress', 'summarise_results')
            self.assertTrue(progress_heard.wait(2))
            return SimpleNamespace(handled=True, response_text='Here is the answer.', open_follow_up=True)

        with (patch.object(coda_runtime, 'runtime_queues', queues),
              patch.object(coda_runtime, 'active_request_state', ActiveRequestState()),
              patch.object(coda_runtime, 'speech_processor', processor),
              patch.object(coda_runtime, 'VoiceAcknowledgement', side_effect=factory),
              patch.object(coda_runtime.command, 'run', side_effect=run),
              patch.object(coda_runtime.dashboard_state, 'record_ai_response') as dashboard,
              patch('builtins.print')):
            execution = Thread(target=coda_runtime._execution_loop, args=(shutdown,))
            speech_worker.start()
            execution.start()
            try:
                queues.requests.put(request)
                self.assertTrue(request.execution_complete.wait(5))
                self.assertTrue(answer_heard.wait(2))
            finally:
                shutdown.set()
                execution.join(2)
                speech_worker.stop()
        self.assertFalse(execution.is_alive())
        self.assertEqual(spoken, [PHRASES[0], "I'm searching the web now.",
                                 "I've found search results. I'm checking their snippets.", 'Here is the answer.'])
        dashboard.assert_called_once_with('Here is the answer.', source='tts')

    def test_search_lifecycle_distinguishes_snippets_from_reading_pages(self):
        ack, _, queues, _, _ = self.make_ack()
        ack.report(ToolFeedback('action', 'mcp.websearch.search_web'))
        action = queues.speech.get(timeout=0)
        self.assertEqual(action.text, "I'm searching the web now.")
        ack.report(ToolFeedback('completed', 'mcp.websearch.search_web', 5))
        self.assertIsNone(queues.speech.get(timeout=0))
        ack.report(ToolFeedback('progress', 'summarise_results'))
        snippet = queues.speech.get(timeout=0)
        self.assertIn('snippets', snippet.text)
        self.assertNotIn('reading', snippet.text)
        ack.report(ToolFeedback('action', 'mcp.webread.read_url'))
        pages = queues.speech.get(timeout=0)
        self.assertIn('reading the selected pages', pages.text)
        for _ in range(20):
            ack.report(ToolFeedback('action', 'mcp.webread.read_url'))
            ack.report(ToolFeedback('progress', 'summarise_results'))
        self.assertIsNone(queues.speech.get(timeout=0))
        ack.finish()
        for task in (action, snippet, pages):
            self.assertTrue(task.cancel_event.is_set())
            self.assertFalse(task.open_follow_up)

    def test_file_and_unknown_tools_get_safe_action_and_processing_messages(self):
        for tool, phrase in (('read_file', "I'm reading the file now."),
                             ('unfamiliar_tool', "I'm running that tool now."),
                             ('delete_path', "I'm deleting it now.")):
            with self.subTest(tool=tool):
                ack, _, queues, _, _ = self.make_ack()
                ack.report(ToolFeedback('action', tool))
                self.assertEqual(queues.speech.get(timeout=0).text, phrase)
                ack.report(ToolFeedback('completed', tool))
                ack.report(ToolFeedback('progress', 'summarise_results'))
                self.assertIn('working', queues.speech.get(timeout=0).text)

    def make_ack(self, source=InputSource.VOICE, **options):
        request = RuntimeRequest("search for something", source)
        queues = RuntimeQueues()
        stop = Mock()
        shutdown = Event()
        ack = VoiceAcknowledgement(request, queues.speech, stop, shutdown, **options)
        self.addCleanup(ack.finish)
        return ack, request, queues, stop, shutdown

    def test_acknowledgement_waits_for_the_delay(self):
        elapsed = [0.0]
        checked_deadline = Event()
        calls = [0]

        def clock():
            calls[0] += 1
            if calls[0] > 1:
                checked_deadline.set()
            return elapsed[0]

        ack, _, queues, _, _ = self.make_ack(clock=clock, delay=0.8)
        ack.start()
        try:
            self.assertTrue(checked_deadline.wait(2))
            self.assertIsNone(queues.speech.get(timeout=0))
            elapsed[0] = 1.0
            self.assertIsNotNone(queues.speech.get(timeout=2))
        finally:
            ack.finish()
            ack._thread.join(timeout=2)

    def test_fallback_feedback_is_bounded_and_retires_with_the_answer(self):
        ack, request, queues, stop, _ = self.make_ack()
        for _ in range(5):
            ack.report('multiple_provider_failures')
        first = queues.speech.get(timeout=0)
        self.assertIn('couple of AI providers', first.text)
        self.assertIsNone(queues.speech.get(timeout=0))
        self.assertTrue(first.is_acknowledgement)
        self.assertFalse(first.open_follow_up)
        ack.finish()
        self.assertTrue(first.cancel_event.is_set())
        self.assertFalse(request.cancel_event.is_set())
        stop.assert_called_once_with(expected_cancel_event=first.cancel_event)
        ack.report('multiple_provider_failures')
        self.assertIsNone(queues.speech.get(timeout=0))

    def test_progress_stays_quiet_for_manual_cancelled_and_shutdown_requests(self):
        for mode in ('manual', 'cancelled', 'shutdown', 'unknown'):
            with self.subTest(mode=mode):
                ack, request, queues, _, shutdown = self.make_ack(
                    source=InputSource.MANUAL if mode == 'manual' else InputSource.VOICE)
                if mode == 'cancelled':
                    request.cancel_event.set()
                elif mode == 'shutdown':
                    shutdown.set()
                ack.report('unknown' if mode == 'unknown' else 'multiple_provider_failures')
                self.assertIsNone(queues.speech.get(timeout=0))

    def test_slow_voice_queues_one_short_filler_with_separate_cancellation(self):
        ack, request, queues, stop, _ = self.make_ack(delay=0, choose=lambda phrases: phrases[1])
        ack.start()
        task = queues.speech.get(timeout=2)
        self.assertIsNotNone(task)
        self.assertEqual(task.text, PHRASES[1])
        self.assertEqual(task.request_id, request.request_id)
        self.assertTrue(task.is_acknowledgement)
        self.assertFalse(task.open_follow_up)
        self.assertIsNone(task.metrics)
        self.assertIsNot(task.cancel_event, request.cancel_event)
        ack.finish()
        ack._thread.join(timeout=2)
        self.assertFalse(ack._thread.is_alive())
        self.assertTrue(task.cancel_event.is_set())
        self.assertFalse(request.cancel_event.is_set())
        stop.assert_called_once_with(expected_cancel_event=task.cancel_event)
        self.assertIsNone(queues.speech.get(timeout=0))

    def test_fast_answer_manual_and_already_cancelled_requests_stay_quiet(self):
        for mode in ("fast", "manual", "cancelled", "shutdown"):
            with self.subTest(mode=mode):
                ack, request, queues, stop, shutdown = self.make_ack(
                    source=InputSource.MANUAL if mode == "manual" else InputSource.VOICE,
                    delay=0,
                )
                if mode == "fast":
                    ack.finish()
                elif mode == "cancelled":
                    request.cancel_event.set()
                elif mode == "shutdown":
                    shutdown.set()
                ack.start()
                if ack._thread is not None:
                    ack._thread.join(timeout=2)
                    self.assertFalse(ack._thread.is_alive())
                self.assertIsNone(queues.speech.get(timeout=0))
                stop.assert_not_called()

    def test_request_cancellation_and_shutdown_interrupt_an_existing_filler(self):
        for reason in ("cancel", "shutdown"):
            with self.subTest(reason=reason):
                ack, request, queues, stop, shutdown = self.make_ack(delay=0)
                ack.start()
                task = queues.speech.get(timeout=2)
                self.assertIsNotNone(task)
                (request.cancel_event if reason == "cancel" else shutdown).set()
                ack._thread.join(timeout=2)
                self.assertFalse(ack._thread.is_alive())
                self.assertTrue(task.cancel_event.is_set())
                stop.assert_called_once_with(expected_cancel_event=task.cancel_event)

    def test_execution_retires_filler_before_buffered_streamed_or_legacy_speech(self):
        for mode in ("buffered", "streamed", "legacy", "error"):
            with self.subTest(mode=mode):
                queues = RuntimeQueues()
                shutdown = Event()
                request = RuntimeRequest("research something", InputSource.VOICE)
                captured = []

                def factory(*args):
                    ack = VoiceAcknowledgement(*args, delay=0)
                    captured.append(ack)
                    return ack

                def run(*_args, **_kwargs):
                    filler = queues.speech.get(timeout=2)
                    self.assertIsNotNone(filler)
                    self.assertTrue(filler.is_acknowledgement)
                    captured.append(filler)
                    if mode == "streamed":
                        current_request().speech_stream.feed("Here is the answer.")
                    elif mode == "legacy":
                        coda_runtime.submit_speech_response("Here is the answer.")
                    elif mode == "error":
                        raise RuntimeError("failed request")
                    if mode in ("streamed", "legacy"):
                        self.assertTrue(filler.cancel_event.is_set())
                    return SimpleNamespace(handled=True, response_text="Here is the answer.",
                                           open_follow_up=True)

                with (patch.object(coda_runtime, "runtime_queues", queues),
                      patch.object(coda_runtime, "active_request_state", ActiveRequestState()),
                      patch.object(coda_runtime, "VoiceAcknowledgement", side_effect=factory),
                      patch.object(coda_runtime.command, "run", side_effect=run),
                      patch.object(coda_runtime.speech_processor, "stop") as stop,
                      patch.object(coda_runtime.dashboard_state, "record_ai_response") as dashboard,
                      patch.dict("os.environ", {"CODA_STREAM_SPEECH": "true"}),
                      patch("builtins.print")):
                    worker = Thread(target=coda_runtime._execution_loop, args=(shutdown,))
                    worker.start()
                    try:
                        queues.requests.put(request)
                        self.assertTrue(request.execution_complete.wait(3))
                    finally:
                        shutdown.set()
                        worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                ack, filler = captured
                ack._thread.join(timeout=2)
                self.assertTrue(filler.cancel_event.is_set())
                self.assertFalse(request.cancel_event.is_set())
                stop.assert_called_once_with(expected_cancel_event=filler.cancel_event)
                if mode != "error":
                    answer = queues.speech.get(timeout=0)
                    self.assertIsNotNone(answer)
                    self.assertFalse(answer.is_acknowledgement)
                    self.assertIs(answer.cancel_event, request.cancel_event)
                    dashboard.assert_called_once_with("Here is the answer.", source="tts")
                else:
                    self.assertIsNone(queues.speech.get(timeout=0))
                    dashboard.assert_not_called()

    def test_late_filler_completion_does_not_change_follow_up_window(self):
        for outcome in (WorkerEventType.COMPLETED, WorkerEventType.CANCELLED, WorkerEventType.FAILED):
            with self.subTest(outcome=outcome):
                queues = RuntimeQueues()
                shutdown = Event()
                state = Mock()
                queues.events.put(WorkerEvent(WorkerName.SPEECH, outcome, "request",
                                              is_acknowledgement=True))
                # Stop after consuming the event; no timing assumptions.
                real_done = queues.events.task_done

                def done():
                    real_done()
                    shutdown.set()

                with (patch.object(coda_runtime, "runtime_queues", queues),
                      patch.object(coda_runtime, "follow_up_state", state),
                      patch.object(queues.events, "task_done", side_effect=done)):
                    coda_runtime._event_loop(shutdown)
                state.open.assert_not_called()
                state.close.assert_not_called()

    def test_speech_worker_preserves_filler_identity_when_skipping_cancelled_audio(self):
        ack, _, queues, _, shutdown = self.make_ack(delay=0)
        ack.start()
        task = queues.speech.get(timeout=2)
        self.assertIsNotNone(task)
        ack.finish()
        queues.speech.complete(task)
        queues.speech.put(task)
        resolve = Mock()
        worker = SpeechWorker(queues.speech, queues.events,
                              SpeechTaskProcessor(resolve, Mock()), shutdown)
        worker.start()
        try:
            started = queues.events.get(timeout=2)
            ended = queues.events.get(timeout=2)
        finally:
            worker.stop()
        self.assertTrue(started.is_acknowledgement)
        self.assertTrue(ended.is_acknowledgement)
        self.assertEqual(ended.event_type, WorkerEventType.CANCELLED)
        self.assertFalse(ended.open_follow_up)
        resolve.assert_not_called()

    def test_answer_interrupts_filler_playback_without_interrupting_request(self):
        queues = RuntimeQueues()
        shutdown = Event()
        request = RuntimeRequest("slow search", InputSource.VOICE)
        controller = SpeechPlaybackController()
        speaking = Event()
        released = Event()
        played = []

        def session_for(text):
            session = Mock()

            def play(cancel):
                played.append(text)
                if text in PHRASES:
                    speaking.set()
                    if not released.wait(2):
                        raise TimeoutError("Filler was not interrupted")
                return not cancel.is_set()

            session.play.side_effect = play
            session.stop.side_effect = released.set
            return session

        provider = Mock(name="fake-tts")
        provider.create_session.side_effect = session_for
        provider.is_available.return_value = True
        processor = SpeechTaskProcessor(lambda: [provider], controller)
        worker = SpeechWorker(queues.speech, queues.events, processor, shutdown)
        ack = VoiceAcknowledgement(request, queues.speech, processor.stop, shutdown, delay=0)
        worker.start()
        try:
            ack.start()
            self.assertTrue(speaking.wait(2))
            ack.finish()
            queues.speech.put(SpeechTask(request.request_id, "Here is the answer.",
                                         request.cancel_event, open_follow_up=True))
            events = [queues.events.get(timeout=2) for _ in range(4)]
        finally:
            ack.finish()
            worker.stop()
        self.assertEqual(len(played), 2)
        self.assertIn(played[0], PHRASES)
        self.assertEqual(played[1], "Here is the answer.")
        self.assertEqual(events[1].event_type, WorkerEventType.CANCELLED)
        self.assertTrue(events[1].is_acknowledgement)
        self.assertEqual(events[3].event_type, WorkerEventType.COMPLETED)
        self.assertTrue(events[3].open_follow_up)
        self.assertFalse(request.cancel_event.is_set())
