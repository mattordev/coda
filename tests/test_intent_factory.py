import json
import os
import unittest
from unittest.mock import patch

from ai.intents.factory import create_router
from tests.intent_fixtures import create_test_registry


class IntentFactoryTests(unittest.TestCase):
    @patch("ai.intents.factory.ollama_provider.generate")
    def test_enabled_classifier_falls_back_to_ollama(self, generate):
        generate.return_value = (
            json.dumps({"intent": "maps", "confidence": 0.9}),
            None,
        )

        with patch.dict(
            os.environ,
            {"CODA_INTENT_LOCAL_CLASSIFIER": "1"},
        ):
            router = create_router(create_test_registry())
            result = router.route("find the station")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "local_classifier")
        self.assertTrue(result.accepted)
        generate.assert_called_once()

    @patch("ai.intents.factory.ollama_provider.generate")
    def test_disabled_classifier_uses_rules_only(self, generate):
        with patch.dict(
            os.environ,
            {"CODA_INTENT_LOCAL_CLASSIFIER": "0"},
        ):
            router = create_router(create_test_registry())
            result = router.route("find the station")

        self.assertFalse(result.matched)
        generate.assert_not_called()

    @patch("ai.intents.factory.ollama_provider.generate")
    def test_exact_match_skips_enabled_classifier(self, generate):
        with patch.dict(
            os.environ,
            {"CODA_INTENT_LOCAL_CLASSIFIER": "1"},
        ):
            router = create_router(create_test_registry())
            result = router.route("maps")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "exact_match")
        generate.assert_not_called()

    @patch("ai.intents.factory.ollama_provider.generate")
    def test_command_prefix_skips_enabled_classifier(self, generate):
        with patch.dict(
            os.environ,
            {"CODA_INTENT_LOCAL_CLASSIFIER": "1"},
        ):
            router = create_router(create_test_registry())
            result = router.route("maps London")

        self.assertEqual(result.intent.name, "maps")
        self.assertEqual(result.strategy, "command_prefix")
        self.assertTrue(result.accepted)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
