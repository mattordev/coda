import unittest
from unittest import mock

from ai.providers import GeminiProvider
from tests.ai_tests.providers.base_test_ai_provider import BaseAIProviderTest


class GeminiProviderTests(BaseAIProviderTest[GeminiProvider]):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName, GeminiProvider)

    def test_confirm_data_is_correct(self) -> None:
        expected_provider_details: GeminiProvider.Details = {
            "type": GeminiProvider.Type.CLOUD,
            "api_key_env": "GEMINI_API_KEY",
            "model_env": "CODA_GEMINI_MODEL",
            "model_required": False,
            "default_model": "gemini-3.7-flash",
            "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        }

        self.assertEqual(expected_provider_details, self.provider_data)

    def test_get_configured_model_uses_default(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self._compare_configured_model("gemini-3.7-flash")


if __name__ == "__main__":
    unittest.main()
