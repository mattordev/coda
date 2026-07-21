import unittest

from ai.intents.defaults import create_builtin_registry
from ai.intents.models import IntentResult
from ai.intents.router import IntentRouter


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
            strategy=self.name,
        )


class BrokenStrategy:
    """A test strategy that simulates an internal detection failure."""

    name = "broken"

    def detect(self, message, registry):
        raise RuntimeError("strategy exploded")


class IntentRouterTests(unittest.TestCase):
    """Tests for the intent router's default exact-match behaviour."""

    def setUp(self):
        # Give every test a fresh registry and router so state cannot leak.
        self.registry = create_builtin_registry()
        self.router = IntentRouter(self.registry)

    def test_routes_canonical_intent_name(self):
        result = self.router.route("maps")

        self.assertTrue(result.matched)
        self.assertIs(result.intent, self.registry.get("maps"))
        self.assertEqual(result.strategy, "exact_match")
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
        self.assertEqual(result.strategy, "maps_match")

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


if __name__ == "__main__":
    unittest.main()
