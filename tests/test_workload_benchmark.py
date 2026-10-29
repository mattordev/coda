"""Offline fixture validation; live model accuracy belongs to benchmark results."""

import argparse
import json
import unittest
from unittest.mock import patch

from ai.intents.local_classifier import _build_classifier_messages
from ai.providers.workloads import classification_options
from benchmarks.llm_workloads import (
    classification_correct, interpretation_messages, local_url, summary_correct,
)
from benchmarks.workload_cases import registry, classification_cases, summary_cases, NATIVE_NAMES


class WorkloadBenchmarkTests(unittest.TestCase):
    def test_fixture_catalogue_uses_every_actual_native_intent_and_structured_mcp(self):
        catalogue = registry()
        names = {item.name for item in catalogue.all()}
        self.assertEqual(names, set(NATIVE_NAMES) | {'mcp.filesystem.read_file', 'mcp.notes.write'})
        selected = {item['expected'] for item in classification_cases()}
        self.assertTrue(set(NATIVE_NAMES).issubset(selected))
        for case in classification_cases():
            with self.subTest(case=case['name']):
                response = json.dumps({'intent': case['expected'], 'confidence': 1 if case['expected'] else 0,
                                       'arguments': case['arguments']})
                self.assertTrue(classification_correct(response, case, catalogue))

    def test_long_dictation_uses_larger_bounded_budget_with_full_catalogue(self):
        case = next(item for item in classification_cases() if item['name'] == 'long_arguments')
        messages = _build_classifier_messages(case['question'], registry())
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(classification_options(messages).max_tokens, 2048)
        payload = json.loads(messages[-1]['content'])
        self.assertIn(case['arguments']['text'], payload['message'])
        self.assertEqual(len(payload['available_intents']), 8)

    def test_unknown_intent_malformed_json_and_incomplete_arguments_fail_evaluation(self):
        case = next(item for item in classification_cases() if item['name'] == 'read')
        for reply in ('{}', '{"intent":', '{"intent":"unknown","confidence":1}',
                      '{"intent":"mcp.filesystem.read_file","confidence":1,"arguments":{}}'):
            self.assertFalse(classification_correct(reply, case, registry()))

    def test_native_score_matches_runtime_which_ignores_model_arguments(self):
        case = next(item for item in classification_cases() if item['name'] == 'maps')
        reply = json.dumps({'intent': 'maps', 'confidence': 1, 'arguments': {'query': 'railway station'}})
        self.assertTrue(classification_correct(reply, case, registry()))

    def test_observed_unsupported_shutdown_low_confidence_cannot_execute_status(self):
        from ai.intents import IntentRouter
        from ai.intents.local_classifier import LocalClassifierStrategy
        reply = json.dumps({'intent': 'status', 'confidence': .6, 'arguments': None})
        router = IntentRouter(registry(), strategies=(LocalClassifierStrategy(lambda messages: (reply, None)),))
        with patch('ai.intents.local_classifier.logger.record_attempt'):
            result = router.route('Shut down this computer')
        self.assertFalse(result.accepted)
        self.assertTrue(result.needs_clarification)

    def test_summary_fixtures_enforce_numbers_and_reject_injected_false_claims(self):
        numeric, untrusted = summary_cases()
        self.assertTrue(summary_correct('Python 3.11 and 3.12; measured value 0.0100.', numeric))
        self.assertFalse(summary_correct('Python thirty-one and thirty-two; value one.', numeric))
        self.assertTrue(summary_correct('Maple supports Linux and does not upload files.', untrusted))
        self.assertFalse(summary_correct('Maple supports Linux and Windows and uploads files.', untrusted))
        self.assertFalse(summary_correct('Maple supports Linux and Windows. No uploads.', untrusted))

    def test_actual_summary_prompt_keeps_numeric_source_and_untrusted_data_boundary(self):
        case = summary_cases()[0]
        messages = interpretation_messages(case['data'], case['question'])
        self.assertEqual([item['role'] for item in messages], ['system', 'user'])
        self.assertIn('3.11 and 3.12', messages[0]['content'])
        self.assertIn('untrusted data', messages[0]['content'])
        payload = json.loads(messages[-1]['content'])
        self.assertEqual(payload['tool_result']['data']['structuredContent'], case['data'])

    def test_live_benchmark_cannot_route_to_cloud_or_credentialed_endpoint(self):
        self.assertEqual(local_url('http://localhost:8080/'), 'http://localhost:8080')
        self.assertEqual(local_url('http://127.0.0.1:8080'), 'http://127.0.0.1:8080')
        self.assertEqual(local_url('http://192.168.0.64:8081'), 'http://192.168.0.64:8081')
        for url in ('https://api.openai.com', 'https://example.com', 'file:///tmp/data',
                    'http://secret@localhost:8080'):
            with self.assertRaises(argparse.ArgumentTypeError):
                local_url(url)


if __name__ == '__main__':
    unittest.main()
