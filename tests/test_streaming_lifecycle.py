"""Generation, speech and final display share a request without fragment commits."""
from contextlib import ExitStack
from threading import Event, Thread
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import coda_runtime
from ai.request_context import current_request, request_context
from ai.providers.generation import GenerationOptions
from ai.llm_router import core as router
from runtime.messages import RuntimeRequest, InputSource, WorkerEventType
from runtime.runtime_queue import RuntimeQueues
from runtime.speech_worker import SpeechTaskProcessor, SpeechWorker
from runtime.speech_playback import SpeechPlaybackController
import utils.llm_service as service


class StreamingLifecycleTests(unittest.TestCase):
    def test_nested_context_retains_stream_and_identity(self):
        marker = object()
        cancel = Event()
        with request_context('outer', cancel, speech_stream=marker):
            with request_context():
                self.assertIs(current_request().speech_stream, marker)
                self.assertEqual(current_request().trace_id, 'outer')
                self.assertIs(current_request().cancel_event, cancel)
        self.assertIsNone(current_request())

    def test_runtime_speaks_early_but_displays_one_final_response(self):
        self._exercise('success')

    def test_cancelled_prefix_is_not_committed_or_displayed_and_next_request_works(self):
        self._exercise('cancel')
        self._exercise('success')

    def test_failed_prefix_is_not_committed_or_replayed(self):
        self._exercise('failure')

    def _exercise(self, mode):
        queues = RuntimeQueues()
        execution_stop = Event()
        speech_stop = Event()
        first_audio = Event()
        generated = Event()
        request = RuntimeRequest('hello', InputSource.MANUAL)
        spoken, contexts = [], []
        final = 'First sentence. Python 3.11 and 0.0100 are literal.'
        class Provider:
            name = 'synthetic'
            def is_available(self):
                return True
            def create_session(self, text):
                def play(cancel):
                    if cancel.is_set():
                        return False
                    spoken.append(text)
                    first_audio.set()
                    return True
                return NS(play=play, stop=lambda: None, is_playing=lambda: False)
        def generate(messages, cancel_event=None, *, options=None):
            contexts.append(current_request())
            options.on_visible_text('First sentence. Python ')
            self.assertTrue(first_audio.wait(2), 'audio must precede completed generation')
            if mode == 'cancel':
                cancel_event.set()
                return None, 'Request cancelled.'
            if mode == 'failure':
                return None, 'connection lost'
            options.on_visible_text('3.11 and 0.0100 are literal.')
            generated.set()
            return final, None
        def command(*args, **kwargs):
            text, error = router.route_request('hello', cancel_event=request.cancel_event)
            return NS(handled=not error, response_text=text, error=error, open_follow_up=True)
        processor = SpeechTaskProcessor(lambda: [Provider()], SpeechPlaybackController(),
                                        normalise_text=lambda text: text)
        speech = SpeechWorker(queues.speech, queues.events, processor, speech_stop)
        with ExitStack() as stack:
            stack.enter_context(patch.object(coda_runtime, 'runtime_queues', queues))
            stack.enter_context(patch.object(coda_runtime.command, 'run', side_effect=command))
            display = stack.enter_context(patch.object(coda_runtime.dashboard_state, 'record_ai_response'))
            stack.enter_context(patch.object(service, 'conversation_log', [service._new_system_message()]))
            stack.enter_context(patch.object(service.registry, 'get_provider_module', return_value=NS(generate=generate)))
            stack.enter_context(patch.object(service.registry, 'get_provider_type', return_value='local'))
            stack.enter_context(patch.object(router, 'get_provider_order', return_value=['fixture', 'fallback']))
            stack.enter_context(patch.object(router.logger, 'should_skip_provider', return_value=False))
            stack.enter_context(patch.object(router.logger, 'record_attempt'))
            stack.enter_context(patch.object(router.logger, 'log_attempt'))
            stack.enter_context(patch.dict('os.environ', {'CODA_STREAM_SPEECH': 'true'}))
            stack.enter_context(patch('builtins.print'))
            worker = Thread(target=coda_runtime._execution_loop, args=(execution_stop,))
            worker.start()
            speech.start()
            try:
                queues.requests.put(request)
                self.assertTrue(request.execution_complete.wait(3))
                speech_thread = speech._thread
                # Wait on the queue's task completion, with a bounded helper.
                drained = Event()
                Thread(target=lambda: (queues.speech.join(), drained.set()), daemon=True).start()
                self.assertTrue(drained.wait(3))
                self.assertEqual(contexts[0].trace_id, request.request_id)
                self.assertIs(contexts[0].cancel_event, request.cancel_event)
                self.assertEqual(len(contexts), 1, 'failed partial answers cannot fall back')
                if mode == 'success':
                    self.assertTrue(generated.is_set())
                    self.assertEqual(spoken, ['First sentence.', 'Python 3.11 and 0.0100 are literal.'])
                    self.assertEqual([entry['content'] for entry in service.conversation_log[1:]], ['hello', final])
                    display.assert_called_once_with(final, source='tts')
                else:
                    self.assertEqual(len(service.conversation_log), 1)
                    display.assert_not_called()
                    self.assertEqual(spoken, ['First sentence.'])
            finally:
                execution_stop.set()
                worker.join(2)
                speech.stop()
            self.assertFalse(worker.is_alive())
            self.assertFalse(speech_thread.is_alive())

    def test_provider_exception_rolls_back_uncommitted_user_turn(self):
        def generate(*args, **kwargs):
            raise TypeError('fixture accepted then failed')
        with patch.object(service, 'conversation_log', [service._new_system_message()]), \
             patch.object(service.registry, 'get_provider_type', return_value='local'):
            with self.assertRaises(TypeError):
                service._generate_provider_response('fixture', NS(generate=generate), 'hello',
                    options=GenerationOptions(on_visible_text=lambda text: None))
            self.assertEqual(len(service.conversation_log), 1)


if __name__ == '__main__':
    unittest.main()
