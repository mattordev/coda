import unittest
from unittest import mock

from ai.providers import GrokProvider

from tests.ai_tests.providers.base_test_ai_provider import BaseAIProviderTestCase


class GrokProviderTests(BaseAIProviderTestCase[GrokProvider]):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName, GrokProvider)

    def test_confirm_data_is_correct(self) -> None:
        expected_provider_details: GrokProvider.Details = {
            "type": GrokProvider.Type.CLOUD,
            "api_key_env": "XAI_API_KEY",
            "model_env": "CODA_GROK_MODEL",
            "model_required": False,
            "default_model": "grok-4.5",
            "default_base_url": "https://api.x.ai/v1",
        }

        self.assertEqual(expected_provider_details, self.provider_data)

    def test_get_configured_model_uses_default(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self._compare_configured_model("grok-4.5")


if __name__ == "__main__":
    unittest.main()
