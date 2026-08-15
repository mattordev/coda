import json
import os
import unittest
from unittest.mock import patch

from ai.intents import Intent
from ai.intents.factory import create_router
from tests.intent_fixtures import create_test_registry


class IntentFactoryTests(unittest.TestCase):
    @patch("ai.providers.ollama.generate")
    def test_enabled_classifier_uses_selected_local_provider(self, generate):
        generate.return_value = (
            json.dumps({"intent": "maps", "confidence": 0.9}),
            None,
        )

        with patch.dict(
            os.environ,
            {
                "CODA_INTENT_LOCAL_CLASSIFIER": "1",
                "CODA_LOCAL_PROVIDERS": "ollama",
            },
        ):
            router = create_router(create_test_registry())
            result = router.route("find the station")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "local_classifier")
        self.assertTrue(result.accepted)
        generate.assert_called_once()

    @patch("ai.providers.ollama.generate")
    def test_disabled_classifier_uses_rules_only(self, generate):
        with patch.dict(
            os.environ,
            {
                "CODA_INTENT_LOCAL_CLASSIFIER": "0",
                "CODA_LOCAL_PROVIDERS": "ollama",
            },
        ):
            router = create_router(create_test_registry())
            result = router.route("find the station")

        self.assertFalse(result.matched)
        generate.assert_not_called()

    @patch("ai.providers.ollama.generate")
    def test_exact_match_skips_enabled_classifier(self, generate):
        with patch.dict(
            os.environ,
            {
                "CODA_INTENT_LOCAL_CLASSIFIER": "1",
                "CODA_LOCAL_PROVIDERS": "ollama",
            },
        ):
            router = create_router(create_test_registry())
            result = router.route("maps")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "exact_match")
        generate.assert_not_called()

    @patch("ai.providers.ollama.generate")
    def test_command_prefix_skips_enabled_classifier(self, generate):
        with patch.dict(
            os.environ,
            {
                "CODA_INTENT_LOCAL_CLASSIFIER": "1",
                "CODA_LOCAL_PROVIDERS": "ollama",
            },
        ):
            router = create_router(create_test_registry())
            result = router.route("maps London")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "command_prefix")
        self.assertTrue(result.accepted)
        generate.assert_not_called()

    @patch("ai.providers.ollama.generate")
    def test_example_match_skips_enabled_classifier(self, generate):
        registry = create_test_registry()
        registry.register(
            Intent(
                name="time",
                description="Report the current time.",
                examples=("What's the time?",),
            )
        )

        with patch.dict(
            os.environ,
            {
                "CODA_INTENT_LOCAL_CLASSIFIER": "1",
                "CODA_LOCAL_PROVIDERS": "ollama",
            },
        ):
            router = create_router(registry)
            result = router.route("WHAT'S THE TIME!")

        self.assertEqual(result.intent.name, "time")
        self.assertEqual(result.strategy, "example_match")
        self.assertTrue(result.accepted)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()