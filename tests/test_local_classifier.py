import json
import unittest
from unittest import mock

from ai.intents.local_classifier import LocalClassifierStrategy
from ai.intents.router import ExactMatchStrategy, IntentRouter
from ai.providers.telemetry import ProviderCallResult
from ai.telemetry.models import (
    AttemptOutcome,
    PrivacyAction,
    ProviderErrorCategory,
    ProviderLocality,
    UsageMetrics,
    WorkloadPurpose,
)
from tests.intent_fixtures import create_test_registry


class FakeGenerator:
    """Record classifier calls and return a configured provider response."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        return self.response, self.error


class LocalClassifierStrategyTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_test_registry()
        record_attempt = mock.patch(
            "ai.intents.local_classifier.logger.record_attempt"
        )
        self.record_attempt = record_attempt.start()
        self.addCleanup(record_attempt.stop)

    def test_records_successful_provider_attempt_with_metadata(self):
        metrics = UsageMetrics(
            total_duration_seconds=0.2,
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
        )
        generator = mock.Mock(return_value=ProviderCallResult(
            response=json.dumps({"intent": "maps", "confidence": 0.9}),
            error=None,
            model="classifier-model",
            metrics=metrics,
        ))
        strategy = LocalClassifierStrategy(generator, provider="ollama")

        result = strategy.detect("find the station", self.registry)

        self.assertEqual(result.intent.name, "maps")
        self.record_attempt.assert_called_once()
        attempt = self.record_attempt.call_args.args[0]
        self.assertTrue(attempt.trace_id)
        self.assertEqual(attempt.sequence, 1)
        self.assertEqual(
            attempt.purpose,
            WorkloadPurpose.INTENT_CLASSIFICATION,
        )
        self.assertEqual(attempt.provider, "ollama")
        self.assertEqual(attempt.model, "classifier-model")
        self.assertEqual(attempt.locality, ProviderLocality.LOCAL)
        self.assertEqual(attempt.outcome, AttemptOutcome.SUCCESS)
        self.assertEqual(attempt.privacy_action, PrivacyAction.RAW)
        self.assertEqual(attempt.metrics, metrics)

    def test_builds_prompt_with_message_and_registered_intents(self):
        generator = FakeGenerator(
            json.dumps({"intent": "maps", "confidence": 0.9})
        )
        strategy = LocalClassifierStrategy(generator)

        strategy.detect("find the station", self.registry)

        self.assertEqual(len(generator.calls), 1)
        messages = generator.calls[0]
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user"],
        )

        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["message"], "find the station")
        self.assertEqual(
            [intent["name"] for intent in payload["available_intents"]],
            [intent.name for intent in self.registry.all()],
        )
        self.assertEqual(
            payload["available_intents"][0]["examples"],
            ["Where is the station?"],
        )
        self.assertIn("parameters", payload["available_intents"][0])

    def test_returns_classified_intent_and_confidence(self):
        generator = FakeGenerator(
            json.dumps({"intent": "maps", "confidence": 0.82})
        )
        strategy = LocalClassifierStrategy(generator)

        result = strategy.detect("where is the station", self.registry)

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.confidence, 0.82)
        self.assertEqual(result.strategy, "local_classifier")
        self.assertFalse(result.accepted)

    def test_accepts_json_inside_markdown_fence(self):
        response = (
            '```json\n'
            '{"intent": "connected", "confidence": 0.8}\n'
            '```'
        )
        strategy = LocalClassifierStrategy(FakeGenerator(response))

        result = strategy.detect("are we online", self.registry)

        self.assertEqual(result.intent.name, "connected")
        self.assertEqual(result.confidence, 0.8)

    def test_returns_unmatched_result_for_null_intent(self):
        response = json.dumps(
            {"intent": None, "confidence": 0.0}
        )
        strategy = LocalClassifierStrategy(FakeGenerator(response))

        result = strategy.detect("write a symphony", self.registry)

        self.assertFalse(result.matched)
        self.assertEqual(result.confidence, 0.0)
        self.assertFalse(result.accepted)

    def test_normalizes_nonzero_confidence_for_null_intent(self):
        response = json.dumps(
            {"intent": None, "confidence": 0.5}
        )
        strategy = LocalClassifierStrategy(FakeGenerator(response))

        result = strategy.detect("write a symphony", self.registry)

        self.assertFalse(result.matched)
        self.assertEqual(result.confidence, 0.0)
        self.assertFalse(result.accepted)

    def test_raises_for_provider_error(self):
        strategy = LocalClassifierStrategy(
            FakeGenerator(error="ollama unavailable")
        )

        with self.assertRaisesRegex(RuntimeError, "ollama unavailable"):
            strategy.detect("find somewhere", self.registry)

        attempt = self.record_attempt.call_args.args[0]
        self.assertEqual(attempt.outcome, AttemptOutcome.FAILURE)
        self.assertEqual(
            attempt.error_category,
            ProviderErrorCategory.PROVIDER_ERROR,
        )

    def test_records_empty_and_invalid_classifier_responses(self):
        cases = (
            ("", AttemptOutcome.EMPTY_RESPONSE, None),
            (
                "not json",
                AttemptOutcome.FAILURE,
                ProviderErrorCategory.INVALID_RESPONSE,
            ),
        )

        for response, outcome, category in cases:
            with self.subTest(response=response):
                self.record_attempt.reset_mock()
                strategy = LocalClassifierStrategy(
                    FakeGenerator(response),
                    provider="ollama",
                )

                with self.assertRaises(ValueError):
                    strategy.detect("find somewhere", self.registry)

                attempt = self.record_attempt.call_args.args[0]
                self.assertEqual(attempt.outcome, outcome)
                self.assertEqual(attempt.error_category, category)

    def test_rejects_invalid_classifier_output(self):
        invalid_outputs = (
            ("", "empty response"),
            ("not json", "invalid JSON"),
            ("[]", "JSON object"),
            (
                json.dumps({"intent": "maps"}),
                "confidence must be a number",
            ),
            (
                json.dumps(
                    {"intent": "maps", "confidence": True}
                ),
                "confidence must be a number",
            ),
            (
                json.dumps(
                    {"intent": None, "confidence": -0.1}
                ),
                "between 0.0 and 1.0",
            ),
            (
                json.dumps(
                    {"intent": None, "confidence": 1.1}
                ),
                "between 0.0 and 1.0",
            ),
            (
                json.dumps(
                    {"intent": "", "confidence": 0.5}
                ),
                "intent must be a name",
            ),
            (
                json.dumps(
                    {"intent": "unknown", "confidence": 0.5}
                ),
                "unknown intent",
            ),
            (
                json.dumps(
                    {"intent": "maps", "confidence": 1.1}
                ),
                "between 0.0 and 1.0",
            ),
        )

        for response, expected_error in invalid_outputs:
            with self.subTest(response=response):
                strategy = LocalClassifierStrategy(
                    FakeGenerator(response)
                )

                with self.assertRaisesRegex(
                    ValueError,
                    expected_error,
                ):
                    strategy.detect(
                        "find somewhere",
                        self.registry,
                    )

    def test_rule_match_skips_local_classifier(self):
        generator = FakeGenerator(
            json.dumps(
                {"intent": "connected", "confidence": 0.9}
            )
        )
        router = IntentRouter(
            self.registry,
            strategies=(
                ExactMatchStrategy(),
                LocalClassifierStrategy(generator),
            ),
        )

        result = router.route("maps")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "exact_match")
        self.assertEqual(generator.calls, [])

    def test_rule_miss_uses_local_classifier_fallback(self):
        generator = FakeGenerator(
            json.dumps({"intent": "maps", "confidence": 0.9})
        )
        router = IntentRouter(
            self.registry,
            strategies=(
                ExactMatchStrategy(),
                LocalClassifierStrategy(generator),
            ),
        )

        result = router.route("find the station")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "local_classifier")
        self.assertTrue(result.accepted)
        self.assertEqual(len(generator.calls), 1)


if __name__ == "__main__":
    unittest.main()
