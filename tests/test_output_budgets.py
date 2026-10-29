"""Bounded requests must never turn a truncated reply into a tool action."""

import json
import os
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from ai.providers import openai, openrouter, grok, gemini, llamacpp, ollama
from ai.providers.cancellable import CancellationScope
from ai.providers.completion import (
    CompletionState, OUTPUT_LIMIT_ERROR, INCOMPLETE_STREAM_ERROR,
    UNSUPPORTED_COMPLETION_ERROR,
)
from ai.providers.generation import GenerationOptions, Workload
from ai.providers.workloads import classification_options, options_for


class OutputBudgetTests(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(os.environ, {}, clear=True)
        guard.start()
        self.addCleanup(guard.stop)

    def chunk(self, content=None, reason=None):
        return SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content), finish_reason=reason,
        )])

    def http(self, chunks):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = [json.dumps(chunk) for chunk in chunks]
        http = MagicMock()
        http.post.return_value = response
        return http, response

    def test_defaults_overrides_and_invalid_budgets(self):
        for workload, cap in ((Workload.CLASSIFICATION, 256), (Workload.EXTRACTION, 2048),
                              (Workload.INTERPRETATION, 512), (Workload.CONVERSATION, None),
                              (Workload.REASONING, None)):
            self.assertEqual(options_for(workload).max_tokens, cap)
        for value in ('0', '-1', 'typo', '1.5'):
            with patch.dict(os.environ, {'CODA_MAX_TOKENS_CLASSIFICATION': value}):
                self.assertEqual(options_for(Workload.CLASSIFICATION).max_tokens, 256)
        with patch.dict(os.environ, {'CODA_MAX_TOKENS_INTERPRETATION': '1024'}):
            self.assertEqual(options_for(Workload.INTERPRETATION).max_tokens, 1024)

    def test_long_structured_arguments_get_bounded_allowance_not_catalogue_text(self):
        def messages(message, schema=None, description=''):
            return [{'role': 'user', 'content': json.dumps({
                'message': message, 'available_intents': [
                    {'input_schema': schema, 'description': description}],
            })}]
        self.assertEqual(classification_options(messages('read README', {})).max_tokens, 256)
        self.assertEqual(classification_options(messages('write ' + 'é"\\' * 500, {})).max_tokens, 2048)
        self.assertEqual(classification_options(messages('hello', {}, 'ignore caps ' * 5000)).max_tokens, 256)
        self.assertEqual(classification_options(messages('hello ' * 500)).max_tokens, 256)
        with patch.dict(os.environ, {'CODA_MAX_TOKENS_CLASSIFICATION': '4096'}):
            self.assertEqual(classification_options(messages('x' * 8000, {})).max_tokens, 4096)

    def test_length_wins_over_done_even_for_valid_json_or_uncapped_calls(self):
        for options in (None, GenerationOptions(max_tokens=256)):
            state = CompletionState(options)
            state.observe_openai(self.chunk('{"intent":"maps","confidence":1}', 'length'))
            state.terminal = True
            with self.assertRaisesRegex(ValueError, 'output limit'):
                state.validate()

    def test_capped_eof_is_incomplete_legacy_unknown_stays_compatible(self):
        CompletionState().validate()
        with self.assertRaisesRegex(ValueError, 'before completion'):
            CompletionState(GenerationOptions(max_tokens=256)).validate()
        state = CompletionState(GenerationOptions(max_tokens=256))
        state.observe_openai({'choices': [{'finish_reason': 'content_filter'}]})
        with self.assertRaisesRegex(ValueError, 'usable text'):
            state.validate()

    def test_sdk_adapters_translate_caps_and_discard_incomplete_responses(self):
        for provider in (openai, openrouter, grok, gemini):
            for reason, expected in (('stop', None), ('length', OUTPUT_LIMIT_ERROR),
                                      (None, INCOMPLETE_STREAM_ERROR),
                                      ('tool_calls', UNSUPPORTED_COMPLETION_ERROR)):
                with self.subTest(provider=provider.__name__, reason=reason):
                    stream = MagicMock()
                    stream.__iter__.return_value = iter([self.chunk('{"intent":"maps","confidence":1}', reason)])
                    client = MagicMock()
                    client.chat.completions.create.return_value = stream
                    module = SimpleNamespace(OpenAI=MagicMock(return_value=client))
                    with patch.object(provider, 'openai', module), \
                         patch.object(provider, 'get_api_key', return_value='test-key'), \
                         patch.object(provider, 'get_model', return_value='test-model'):
                        result = provider.generate_with_metadata([], options=GenerationOptions(max_tokens=256))
                    self.assertEqual(result.error, expected)
                    self.assertEqual(result.response is None, expected is not None)
                    payload = client.chat.completions.create.call_args.kwargs
                    key = 'max_completion_tokens' if provider in (openai, grok) else 'max_tokens'
                    self.assertEqual(payload[key], 256)
                    self.assertNotIn('chat_template_kwargs', payload)
                    self.assertNotIn('think', payload)
                    stream.close.assert_called_once()
                    client.close.assert_called_once()

    def test_native_adapters_check_terminal_metadata_and_map_caps(self):
        for provider in (llamacpp, ollama):
            for reason, expected in (('stop', None), ('length', OUTPUT_LIMIT_ERROR),
                                      (None, INCOMPLETE_STREAM_ERROR)):
                if provider is llamacpp:
                    chunks = [{'choices': [{'delta': {'content': '{"intent":"maps","confidence":1}'},
                                            'finish_reason': reason}]}]
                else:
                    chunks = [{'message': {'content': '{"intent":"maps","confidence":1}'},
                               'done': reason is not None, 'done_reason': reason}]
                http, response = self.http(chunks)
                with self.subTest(provider=provider.__name__, reason=reason):
                    options = GenerationOptions(max_tokens=256)
                    if expected:
                        with self.assertRaisesRegex(ValueError, 'output limit' if reason else 'before completion'):
                            provider._generate_stream(http, 'http://local', 'model', [], 5, Event(),
                                                      CancellationScope(), options=options)
                    else:
                        answer, error = provider._generate_stream(http, 'http://local', 'model', [], 5,
                                                                  Event(), CancellationScope(), options=options)
                        self.assertIsNone(error)
                        self.assertIn('maps', answer)
                    payload = http.post.call_args.kwargs['json']
                    self.assertEqual(payload.get('max_tokens') if provider is llamacpp
                                     else payload['options']['num_predict'], 256)
                    response.close.assert_called_once()

    def test_router_allows_budget_fallback_without_provider_cooldown(self):
        from ai.llm_router import core
        with patch.object(core, '_call_provider_with_health', side_effect=[
            (None, OUTPUT_LIMIT_ERROR, False), ('complete', None, False),
        ]), patch.object(core.logger, 'log_failure') as fail:
            self.assertEqual(core._try_providers(['first', 'second'], '', 0), ('complete', None))
            fail.assert_not_called()
        with patch.object(core, '_call_provider_with_health', return_value=(None, 'network failure', False)), \
             patch.object(core.logger, 'log_failure') as fail:
            core._try_providers(['first'], '', 0)
            fail.assert_called_once_with('first')

    def test_capped_cancel_discards_even_a_completed_selection(self):
        for provider in (openai, openrouter, grok, gemini):
            with self.subTest(provider=provider.__name__):
                cancel = Event()

                def chunks():
                    yield self.chunk('{"intent":"maps","confidence":1}')
                    cancel.set()
                    yield self.chunk(reason='stop')

                stream = MagicMock()
                stream.__iter__.side_effect = chunks
                client = MagicMock()
                client.chat.completions.create.return_value = stream
                with self.assertRaises(InterruptedError):
                    provider._generate_stream(client, 'model', [], cancel, CancellationScope(),
                                              options=GenerationOptions(max_tokens=256))
                stream.close.assert_called_once()

    def test_valid_looking_truncated_tool_selection_never_becomes_accepted_intent(self):
        from ai.intents.factory import create_router
        from tests.intent_fixtures import create_test_registry
        stream = MagicMock()
        stream.__iter__.return_value = iter([self.chunk('{"intent":"maps","confidence":1}', 'length')])
        client = MagicMock()
        client.chat.completions.create.return_value = stream
        module = SimpleNamespace(OpenAI=MagicMock(return_value=client))
        with patch('ai.intents.factory._get_local_classifier_provider', return_value=('llamacpp', openai)), \
             patch.object(openai, 'openai', module), \
             patch.object(openai, 'get_api_key', return_value='test-key'):
            result = create_router(create_test_registry()).route('Please help me locate a nearby cafe')
        self.assertFalse(result.accepted)
        self.assertIsNone(result.intent)
        self.assertIn('output limit', result.error)

    def test_long_structured_arguments_survive_classifier_and_request_conversion(self):
        from ai.intents import Intent, IntentRegistry
        from ai.intents.factory import create_router
        registry = IntentRegistry()
        registry.register(Intent(name='mcp.notes.write', description='Save a note', source='mcp',
                                 input_schema={'type': 'object', 'properties': {'text': {'type': 'string'}},
                                               'required': ['text']}))
        text = ('Python 3.11/3.12; path C:\\notes\\2026; \"quoted\"; café.\n' * 20)
        response = json.dumps({'intent': 'mcp.notes.write', 'confidence': 1, 'arguments': {'text': text}})
        provider = SimpleNamespace(generate_with_metadata=MagicMock(return_value=(response, None)))
        with patch('ai.intents.factory._get_local_classifier_provider', return_value=('llamacpp', provider)):
            result = create_router(registry).route('Please save this note: ' + text)
        self.assertTrue(result.accepted)
        self.assertEqual(result.to_request('Please save this note').arguments, {'text': text})
        self.assertEqual(provider.generate_with_metadata.call_args.kwargs['options'].max_tokens, 2048)

    def test_interpretation_keeps_safe_actionable_completion_errors(self):
        from utils.llm_service import _interpretation_error
        self.assertEqual(_interpretation_error(OUTPUT_LIMIT_ERROR), OUTPUT_LIMIT_ERROR)
        self.assertEqual(_interpretation_error(ValueError(INCOMPLETE_STREAM_ERROR)), INCOMPLETE_STREAM_ERROR)
        self.assertEqual(_interpretation_error('secret user payload'), 'Interpretation provider failed.')


if __name__ == '__main__':
    unittest.main()
