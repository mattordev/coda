import unittest

from ai.intents.models import Intent, IntentResult
from ai.intents.registry import IntentRegistry
from ai.intents.router import (
    CommandPrefixStrategy,
    ExampleMatchStrategy,
    IntentRouter,
)
from tests.intent_fixtures import create_test_registry


class NoMatchStrategy:
    """A test strategy that deliberately declines every message."""

    name = "no_match"

    def detect(self, message, registry):
        return IntentResult(intent=None)


class MapsMatchStrategy:
    """A test strategy that always selects the maps intent."""

    name = "maps_match"

    def detect(self, message, registry):
        return IntentResult(
            intent=registry.get("maps"),
            confidence=0.90,
            strategy=self.name,
        )


class ScoredIntentStrategy:
    """A configurable strategy used to exercise confidence thresholds."""

    def __init__(self, name, intent_name, confidence):
        self.name = name
        self.intent_name = intent_name
        self.confidence = confidence

    def detect(self, message, registry):
        return IntentResult(
            intent=registry.get(self.intent_name),
            confidence=self.confidence,
            strategy=self.name,
        )


class BrokenStrategy:
    """A test strategy that simulates an internal detection failure."""

    name = "broken"

    def detect(self, message, registry):
        raise RuntimeError("strategy exploded")


class CommandPrefixStrategyTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_test_registry()
        self.strategy = CommandPrefixStrategy()

    def test_matches_intent_name_and_alias_at_start_of_message(self):
        cases = (
            ("maps London", "maps"),
            ("debug on", "debug"),
            ("repeat Mind the gap", "say"),
            ("read aloud Platform nine", "say"),
        )

        for message, expected_intent in cases:
            with self.subTest(message=message):
                result = self.strategy.detect(message, self.registry)

                self.assertEqual(result.intent.name, expected_intent)
                self.assertEqual(result.confidence, 1.0)
                self.assertEqual(result.strategy, "command_prefix")

    def test_does_not_match_trigger_inside_message(self):
        messages = (
            "please say hello",
            "What's the status of that project?",
        )

        for message in messages:
            with self.subTest(message=message):
                result = self.strategy.detect(message, self.registry)

                self.assertFalse(result.matched)


class ExampleMatchStrategyTests(unittest.TestCase):
    def setUp(self):
        self.intent = Intent(
            name="time",
            description="Report the current time.",
            examples=(
                "What's the time?",
                "Tell me the time.",
                "Current time.",
                "What time is it?",
            ),
        )
        self.registry = IntentRegistry()
        self.registry.register(self.intent)
        self.strategy = ExampleMatchStrategy()

    def test_maps_declared_phrasings_to_same_intent(self):
        for message in self.intent.examples:
            with self.subTest(message=message):
                result = self.strategy.detect(message, self.registry)

                self.assertIs(result.intent, self.intent)
                self.assertEqual(result.confidence, 1.0)
                self.assertEqual(result.strategy, "example_match")

    def test_returns_unmatched_result_for_unknown_phrase(self):
        result = self.strategy.detect("Set an alarm.", self.registry)

        self.assertFalse(result.matched)


class IntentRouterTests(unittest.TestCase):
    """Tests for the intent router's default exact-match behaviour."""

    def setUp(self):
        # Give every test a fresh registry and router so state cannot leak.
        self.registry = create_test_registry()
        self.router = IntentRouter(self.registry)

    def test_routes_canonical_intent_name(self):
        result = self.router.route("maps")

        self.assertTrue(result.matched)
        self.assertIs(result.intent, self.registry.get("maps"))
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(result.strategy, "exact_match")
        self.assertTrue(result.accepted)
        self.assertFalse(result.needs_clarification)
        self.assertIsNone(result.error)

    def test_routes_normalized_alias(self):
        result = self.router.route(" NAVIGATE ")

        self.assertTrue(result.matched)
        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "exact_match")

    def test_returns_unmatched_result_for_unknown_message(self):
        result = self.router.route("make me a sandwich")

        self.assertFalse(result.matched)
        self.assertIsNone(result.intent)
        self.assertIsNone(result.strategy)
        self.assertIsNone(result.error)

    def test_returns_error_for_empty_message(self):
        result = self.router.route("   ")

        self.assertFalse(result.matched)
        self.assertIsNone(result.intent)
        self.assertEqual(result.error, "Message cannot be empty.")

    def test_tries_later_strategy_when_first_does_not_match(self):
        router = IntentRouter(
            self.registry,
            strategies=(NoMatchStrategy(), MapsMatchStrategy()),
        )

        result = router.route("find somewhere")

        self.assertTrue(result.matched)
        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.confidence, 0.90)
        self.assertEqual(result.strategy, "maps_match")
        self.assertTrue(result.accepted)

    def test_continues_after_strategy_raises_exception(self):
        router = IntentRouter(
            self.registry,
            strategies=(BrokenStrategy(), MapsMatchStrategy()),
        )

        result = router.route("find somewhere")

        self.assertTrue(result.matched)
        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "maps_match")
        self.assertIsNone(result.error)

    def test_returns_error_when_all_strategies_fail(self):
        router = IntentRouter(
            self.registry,
            strategies=(BrokenStrategy(),),
        )

        result = router.route("find somewhere")

        self.assertFalse(result.matched)
        self.assertIsNone(result.intent)
        self.assertEqual(result.error, "broken: strategy exploded")

    def test_accepts_candidate_at_acceptance_threshold(self):
        strategy = ScoredIntentStrategy("boundary", "maps", 0.75)
        router = IntentRouter(self.registry, strategies=(strategy,))

        result = router.route("find somewhere")

        self.assertTrue(result.matched)
        self.assertTrue(result.accepted)
        self.assertFalse(result.needs_clarification)
        self.assertEqual(result.confidence, 0.75)

    def test_requests_clarification_at_clarification_threshold(self):
        strategy = ScoredIntentStrategy("boundary", "maps", 0.40)
        router = IntentRouter(self.registry, strategies=(strategy,))

        result = router.route("find somewhere")

        self.assertTrue(result.matched)
        self.assertFalse(result.accepted)
        self.assertTrue(result.needs_clarification)
        self.assertEqual(result.confidence, 0.40)

    def test_ignores_candidate_below_clarification_threshold(self):
        strategy = ScoredIntentStrategy("weak", "maps", 0.39)
        router = IntentRouter(self.registry, strategies=(strategy,))

        result = router.route("find somewhere")

        self.assertFalse(result.matched)
        self.assertFalse(result.accepted)
        self.assertFalse(result.needs_clarification)
        self.assertEqual(result.confidence, 0.0)

    def test_returns_strongest_clarification_candidate(self):
        strategies = (
            ScoredIntentStrategy("weaker", "maps", 0.50),
            ScoredIntentStrategy("stronger", "connected", 0.65),
        )
        router = IntentRouter(self.registry, strategies=strategies)

        result = router.route("ambiguous request")

        self.assertEqual(result.intent.name, "connected")
        self.assertEqual(result.strategy, "stronger")
        self.assertEqual(result.confidence, 0.65)
        self.assertFalse(result.accepted)
        self.assertTrue(result.needs_clarification)

    def test_keeps_earlier_candidate_when_scores_are_equal(self):
        strategies = (
            ScoredIntentStrategy("first", "maps", 0.60),
            ScoredIntentStrategy("second", "connected", 0.60),
        )
        router = IntentRouter(self.registry, strategies=strategies)

        result = router.route("ambiguous request")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "first")

    def test_uses_custom_confidence_thresholds(self):
        strategy = ScoredIntentStrategy("custom", "maps", 0.80)
        router = IntentRouter(
            self.registry,
            strategies=(strategy,),
            acceptance_threshold=0.90,
            clarification_threshold=0.60,
        )

        result = router.route("find somewhere")

        self.assertTrue(result.matched)
        self.assertFalse(result.accepted)
        self.assertTrue(result.needs_clarification)

    def test_rejects_invalid_confidence_thresholds(self):
        invalid_thresholds = (
            (-0.1, 0.0),
            (1.1, 0.4),
            (0.5, 0.6),
        )

        for acceptance, clarification in invalid_thresholds:
            with self.subTest(
                acceptance=acceptance,
                clarification=clarification,
            ):
                with self.assertRaisesRegex(ValueError, "thresholds"):
                    IntentRouter(
                        self.registry,
                        acceptance_threshold=acceptance,
                        clarification_threshold=clarification,
                    )


class IntentResultTests(unittest.TestCase):
    """Tests for confidence-related IntentResult invariants."""

    def test_rejects_confidence_outside_valid_range(self):
        for confidence in (-0.1, 1.1):
            with self.subTest(confidence=confidence):
                with self.assertRaisesRegex(ValueError, "Confidence"):
                    IntentResult(intent=None, confidence=confidence)

    def test_rejects_accepted_result_without_intent(self):
        with self.assertRaisesRegex(ValueError, "accepted result"):
            IntentResult(intent=None, accepted=True)


if __name__ == "__main__":
    unittest.main()
