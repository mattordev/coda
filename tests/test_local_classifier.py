import json
import unittest

from ai.intents.local_classifier import LocalClassifierStrategy
from ai.intents.router import ExactMatchStrategy, IntentRouter
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