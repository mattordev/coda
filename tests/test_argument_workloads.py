"""Standalone extraction observes the same request policy as classification."""

from dataclasses import asdict
import json
import os
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai.intents import Intent, IntentResult
from ai.providers.completion import OUTPUT_LIMIT_ERROR
from ai.providers.generation import Workload
from ai.providers.telemetry import ProviderCallResult
from ai.request_context import request_context
from ai.telemetry.models import AttemptOutcome, UsageMetrics
from ai.tools import arguments


class ArgumentWorkloadTests(unittest.TestCase):
    def setUp(self):
        self.intent = Intent('mcp.notes.save', 'Save supplied text', source='mcp', input_schema={
            'type': 'object', 'properties': {'text': {'type': 'string'}},
            'required': ['text'], 'additionalProperties': False,
        })
        self.selection = IntentResult(self.intent, 1, accepted=True)
        self.generate = Mock(return_value=('{}', None))
        self.provider = SimpleNamespace(generate_with_metadata=self.generate)
        self.records = []
        for guard in (
            patch.object(arguments, '_get_local_classifier_provider', return_value=('llamacpp', self.provider)),
            patch.object(arguments.logger, 'record_attempt', side_effect=self.records.append),
            patch.dict(os.environ, {}, clear=True),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def test_long_extraction_keeps_exact_values_policy_metadata_and_shared_identity(self):
        text = ('Python 3.11/3.12; 0.0100; café; C:\\notes\\2026; id=66f8a8ba. ' * 20)
        self.generate.return_value = ProviderCallResult(json.dumps({'text': text}), None, 'fixture',
                                                       UsageMetrics(output_tokens=400))
        cancel = Event()
        with request_context('runtime-id', cancel):
            self.assertEqual(arguments.prepare_arguments(self.selection, 'Save ' + text), {'text': text})
        options = self.generate.call_args.kwargs['options']
        self.assertEqual(options.workload, Workload.EXTRACTION)
        self.assertIs(options.thinking, False)
        self.assertEqual(options.max_tokens, 2048)
        self.assertIs(self.generate.call_args.kwargs['cancel_event'], cancel)
        self.assertEqual(self.records[0].trace_id, 'runtime-id')
        self.assertEqual(self.records[0].purpose.value, 'argument_extraction')
        self.assertEqual(self.records[0].metrics.output_tokens, 400)
        self.assertNotIn('café', json.dumps(asdict(self.records[0])))

    def test_explicit_values_json_and_native_commands_skip_model_and_telemetry(self):
        selection = IntentResult(self.intent, 1, accepted=True, arguments={'text': 'Python 3.11'})
        self.assertEqual(arguments.prepare_arguments(selection, 'save'), {'text': 'Python 3.11'})
        self.assertEqual(arguments.prepare_arguments(self.selection, 'mcp.notes.save {"text":"0.0100"}'),
                         {'text': '0.0100'})
        self.assertIsNone(arguments.prepare_arguments(IntentResult(Intent('time', 'Time'), 1, accepted=True), 'time'))
        self.generate.assert_not_called()
        self.assertEqual(self.records, [])

    def test_legacy_messages_adapter_is_called_once_without_options(self):
        called = []

        def legacy(messages, cancel_event=None):
            called.append(cancel_event)
            return '{"text":"hello"}', None

        self.provider = SimpleNamespace(generate=legacy)
        with patch.object(arguments, '_get_local_classifier_provider', return_value=('llamacpp', self.provider)):
            self.assertEqual(arguments.prepare_arguments(self.selection, 'Save hello'), {'text': 'hello'})
        self.assertEqual(called, [None])

    def test_truncated_valid_json_invalid_schema_and_provider_typeerror_never_replay(self):
        for result in (('{"text":"looks complete"}', OUTPUT_LIMIT_ERROR), ('{', None), ('{}', None),
                       ('{"text":7}', None), (None, 'private provider error')):
            with self.subTest(result=result):
                self.generate.reset_mock()
                self.generate.return_value = result
                with self.assertRaisesRegex(ValueError, 'Arguments could not'):
                    arguments.prepare_arguments(self.selection, 'Save note')
                self.assertEqual(self.generate.call_count, 1)
                self.assertEqual(self.records[-1].outcome, AttemptOutcome.FAILURE)
        self.generate.side_effect = TypeError('failure after accepting request')
        self.generate.reset_mock()
        with self.assertRaises(ValueError):
            arguments.prepare_arguments(self.selection, 'Save note')
        self.assertEqual(self.generate.call_count, 1)

    def test_cancellation_before_and_after_generation_never_returns_arguments(self):
        cancel = Event()
        cancel.set()
        with self.assertRaisesRegex(ValueError, 'Cancelled'):
            arguments.prepare_arguments(self.selection, 'Save note', cancel_event=cancel)
        self.generate.assert_not_called()
        cancel.clear()

        def late(*args, **kwargs):
            cancel.set()
            return '{"text":"late"}', None

        self.generate.side_effect = late
        with self.assertRaisesRegex(ValueError, 'Cancelled'):
            arguments.prepare_arguments(self.selection, 'Save note', cancel_event=cancel)
        self.assertEqual(self.records[-1].outcome, AttemptOutcome.CANCELLED)


if __name__ == '__main__':
    unittest.main()
