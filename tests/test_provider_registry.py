import unittest

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


if __name__ == "__main__":
    unittest.main()