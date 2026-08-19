import unittest
from unittest.mock import patch

from ai.providers import registry


class ProviderRegistryTests(unittest.TestCase):
    def test_openai_metadata(self):
        self.assertEqual(
            registry.get_provider_api_key_env("openai"),
            "OPENAI_API_KEY",
        )
        self.assertEqual(
            registry.get_provider_model_env("openai"),
            "CODA_OPENAI_MODEL",
        )
        self.assertIsNone(
            registry.get_provider_base_url_env("openai")
        )

    def test_gemini_metadata(self):
        self.assertEqual(
            registry.get_provider_api_key_env("gemini"),
            "GEMINI_API_KEY",
        )
        self.assertEqual(
            registry.get_provider_model_env("gemini"),
            "CODA_GEMINI_MODEL",
        )
        self.assertIsNone(
            registry.get_provider_base_url_env("gemini")
        )

    def test_ollama_metadata(self):
        self.assertIsNone(
            registry.get_provider_api_key_env("ollama")
        )
        self.assertEqual(
            registry.get_provider_model_env("ollama"),
            "CODA_OLLAMA_MODEL",
        )
        self.assertEqual(
            registry.get_provider_base_url_env("ollama"),
            "CODA_OLLAMA_BASE_URL",
        )

    def test_llamacpp_metadata(self):
        self.assertIsNone(
            registry.get_provider_api_key_env("llamacpp")
        )
        self.assertEqual(
            registry.get_provider_model_env("llamacpp"),
            "CODA_LLAMACPP_MODEL",
        )
        self.assertEqual(
            registry.get_provider_base_url_env("llamacpp"),
            "CODA_LLAMACPP_BASE_URL",
        )

    def test_unknown_provider_metadata_returns_none(self):
        self.assertIsNone(
            registry.get_provider_api_key_env("does-not-exist")
        )
        self.assertIsNone(
            registry.get_provider_model_env("does-not-exist")
        )
        self.assertIsNone(
            registry.get_provider_base_url_env("does-not-exist")
        )

    def test_gemini_is_supported(self):
        self.assertTrue(
            registry.is_provider_supported("gemini")
        )

    def test_gemini_is_configured_when_api_key_exists(self):
        with patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "test-key"},
            clear=True,
        ):
            self.assertTrue(
                registry.is_provider_configured("gemini")
            )
            self.assertEqual(
                registry.get_provider_type("gemini"),
                "cloud",
            )

    def test_grok_metadata(self):
        self.assertEqual(
            registry.get_provider_api_key_env("grok"),
            "XAI_API_KEY",
        )
        self.assertEqual(
            registry.get_provider_model_env("grok"),
            "CODA_GROK_MODEL",
        )
        self.assertIsNone(
            registry.get_provider_base_url_env("grok")
        )

    def test_grok_is_supported(self):
        self.assertTrue(
            registry.is_provider_supported("grok")
        )

    def test_grok_is_configured_when_api_key_exists(self):
        with patch.dict(
            "os.environ",
            {"XAI_API_KEY": "test-key"},
            clear=True,
        ):
            self.assertTrue(
                registry.is_provider_configured("grok")
            )
            self.assertEqual(
                registry.get_provider_type("grok"),
                "cloud",
            )

    def test_openrouter_metadata(self):
        self.assertEqual(
            registry.get_provider_api_key_env("openrouter"),
            "OPENROUTER_API_KEY",
        )
        self.assertEqual(
            registry.get_provider_model_env("openrouter"),
            "CODA_OPENROUTER_MODEL",
        )
        self.assertIsNone(
            registry.get_provider_base_url_env("openrouter")
        )

    def test_openrouter_is_supported(self):
        self.assertTrue(
            registry.is_provider_supported("openrouter")
        )

    def test_openrouter_is_configured_when_api_key_and_model_exist(self):
        with patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
                "CODA_OPENROUTER_MODEL": "openrouter/free",
            },
            clear=True,
        ):
            self.assertTrue(
                registry.is_provider_configured("openrouter")
            )
            self.assertEqual(
                registry.get_provider_type("openrouter"),
                "cloud",
            )

    def test_openrouter_is_not_configured_when_model_is_missing(self):
        with patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
                "CODA_OPENROUTER_MODEL": "",
            },
            clear=True,
        ):
            self.assertFalse(
                registry.is_provider_configured("openrouter")
            )


if __name__ == "__main__":
    unittest.main()