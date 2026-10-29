"""Console/history keep facts; providers receive a separate pronunciation copy."""

import os
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from runtime.messages import SpeechTask, WorkerEventType
from runtime.speech_playback import SpeechPlaybackController
from runtime.speech_worker import SpeechTaskProcessor
from tts.spoken_text import normalise_spoken_text
import utils.llm_service as history
import utils.speak_response as speech


SEMANTIC = 'Python 3.11 and 3.12; version 1.5.1; value 0.0100; file "README.md".'


class SemanticFidelityTests(unittest.TestCase):
    def test_conflicting_custom_speech_prompt_keeps_mandatory_factual_rule_last(self):
        custom = 'Expand numbers and versions into words for speech. '
        with patch.dict(os.environ, {'CODA_SYSTEM_PROMPT': custom * 200}):
            for limit in (None, 4000):
                prompt = history._get_system_prompt(max_chars=limit)
                self.assertTrue(prompt.startswith(custom))
                self.assertTrue(prompt.endswith(history.SEMANTIC_FIDELITY_POLICY))
                self.assertIn('untrusted observations', prompt)
                self.assertIn('3.11 and 3.12', prompt)
                if limit is not None:
                    self.assertLessEqual(len(prompt), limit)

    def test_service_and_history_preserve_exact_versions_paths_ids_and_notation(self):
        semantic = SEMANTIC + r' C:\Python311\main.py; request_id=66f8a8ba; 1e-3; 2026-10-29.'
        provider = SimpleNamespace(generate_with_metadata=Mock(return_value=(semantic, None)))
        with patch.object(history, 'conversation_log', [history._new_system_message()]), \
             patch.object(history.registry, 'get_provider_type', return_value='local'):
            answer, error = history._generate_provider_response('fixture', provider, 'Read the facts')
            self.assertIsNone(error)
            self.assertEqual(answer, semantic)
            self.assertEqual(history.conversation_log[-1]['content'], semantic)
            self.assertNotIn('three point', history.conversation_log[-1]['content'])
        messages = provider.generate_with_metadata.call_args.args[0]
        self.assertEqual(sum(item['role'] == 'system' for item in messages), 1)
        self.assertIn(history.SEMANTIC_FIDELITY_POLICY, messages[0]['content'])

    def test_runtime_speech_normalises_only_playback_copy(self):
        provider = SimpleNamespace(name='fixture', is_available=lambda: True)
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.return_value = True
        task = SpeechTask('facts', SEMANTIC, Event())
        event = SpeechTaskProcessor(lambda: [provider], controller).process(task)
        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        self.assertEqual(task.text, SEMANTIC)
        spoken = ' '.join(call.args[1] for call in controller.play.call_args_list)
        self.assertIn('three point one one and three point one two', spoken)
        self.assertIn('zero point zero one zero zero', spoken)
        self.assertIn('README dot markdown', spoken)

    def test_synchronous_and_queued_speech_keep_dashboard_text_semantic(self):
        provider = Mock()
        provider.is_available.return_value = True
        provider.create_session.return_value.play.return_value = True
        for queued in (False, True):
            submit = Mock(return_value=True) if queued else None
            with patch.object(speech, '_speech_submitter', submit), \
                 patch.object(speech, 'resolve_speech_providers', return_value=[provider]), \
                 patch.object(speech.dashboard_state, 'record_ai_response') as record:
                self.assertTrue(speech.speak_response(SEMANTIC))
            record.assert_called_once_with(SEMANTIC, source='tts')
            if queued:
                submit.assert_called_once_with(SEMANTIC)
            else:
                provider.create_session.assert_called_once_with(normalise_spoken_text(SEMANTIC))

    def test_technical_notation_paths_and_identifiers_are_not_guessed(self):
        text = r'C:\Python311\3.12\main.py https://example.com/v3.11?id=17 request_31 1e-3 192.168.1.42 2026-10-29'
        self.assertEqual(normalise_spoken_text(text), text)
        self.assertEqual(normalise_spoken_text('Python 3.11 and 3.12.'),
                         'Python three point one one and three point one two.')
        self.assertEqual(normalise_spoken_text('version 3.12.00.'), 'version three point one two point zero zero.')
        normal = normalise_spoken_text(SEMANTIC)
        self.assertEqual(normalise_spoken_text(normal), normal)


if __name__ == '__main__':
    unittest.main()
