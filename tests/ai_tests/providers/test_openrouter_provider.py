import unittest
from unittest import mock

from ai.providers import OpenRouterProvider
from tests.ai_tests.providers.base_test_ai_provider import BaseAIProviderTest


class OpenRouterProviderTests(BaseAIProviderTest[OpenRouterProvider]):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName, OpenRouterProvider)

    def test_confirm_data_is_correct(self) -> None:
        expected_provider_details: OpenRouterProvider.Details = {
            "type": OpenRouterProvider.Type.CLOUD,
            "api_key_env": "OPENROUTER_API_KEY",
            "model_env": "CODA_OPENROUTER_MODEL",
            "model_required": True,
            "default_base_url": "https://openrouter.ai/api/v1",
        }

        self.assertEqual(expected_provider_details, self.provider_data)

    def test_get_configured_model_uses_configured_value(self) -> None:
        model_name = "openai/gpt-4.1-mini"
        model_env = self.provider_data.get("model_env")

        with mock.patch.dict("os.environ", {model_env: model_name}):
            self._compare_configured_model(model_name)

    def test_get_configured_model_has_no_default(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self._compare_configured_model("")

    def test_get_api_key(self) -> None:
        self.test_get_api_key()


if __name__ == "__main__":
    unittest.main()
