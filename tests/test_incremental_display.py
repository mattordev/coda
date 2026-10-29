"""Terminal previews are optional, cancellable and never duplicated as final text."""
from threading import Event
import unittest
from unittest.mock import Mock, patch

from ai.request_context import request_context
from ai.providers.generation import GenerationOptions
from ai.llm_router import core as router
from runtime.incremental_display import IncrementalDisplay
import utils.on_command as command


class IncrementalDisplayTests(unittest.TestCase):
    def test_order_control_filter_and_final_newline(self):
        output = []
        display = IncrementalDisplay(Event(), write=output.append)
        display.feed('First ')
        self.assertEqual(''.join(output), 'CODA: First ')
        display.feed('answer.\x1b\x00\x7f\x9b')
        display.finish(True)
        display.finish(True)
        self.assertEqual(''.join(output), 'CODA: First answer.\n')
        with self.assertRaises(InterruptedError):
            display.feed('late')

    def test_cancel_and_failure_mark_preview_incomplete(self):
        for cancel_it in (False, True):
            output = []
            cancel = Event()
            display = IncrementalDisplay(cancel, write=output.append)
            display.feed('Partial')
            if cancel_it:
                cancel.set()
                with self.assertRaises(InterruptedError):
                    display.feed('late')
            display.finish(False)
            self.assertEqual(''.join(output), 'CODA: Partial\n[Response incomplete.]\n')

    def test_display_and_speech_callbacks_are_independent(self):
        for speech_on, display_on in ((True, False), (False, True), (True, True)):
            speech = Mock() if speech_on else None
            display = Mock() if display_on else None
            def call(*args, **kwargs):
                kwargs['options'].on_visible_text('Answer.')
                return 'Answer.', None, False
            with request_context(speech_stream=speech, display_stream=display), \
                 patch.object(router, 'get_provider_order', return_value=['fixture']), \
                 patch.object(router, '_call_provider_with_health', side_effect=call):
                self.assertEqual(router.route_request('hello'), ('Answer.', None))
            for sink in (speech, display):
                if sink is not None:
                    sink.feed.assert_called_once_with('Answer.')

    def test_on_command_does_not_print_final_answer_twice(self):
        display = IncrementalDisplay(Event(), write=lambda text: None)
        def run(*args, **kwargs):
            display.feed('Answer.')
            return command.CommandResult(True, response_text='Answer.')
        with request_context(display_stream=display), \
             patch.object(command, '_on_command', side_effect=run), patch('builtins.print') as printed:
            result = command.on_command('hello', {})
        self.assertEqual(result.response_text, 'Answer.')
        printed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
