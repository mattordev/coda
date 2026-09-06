from ai.providers.openai import OpenAIProvider
from ai.providers.provider import Provider

from ai.providers.provider import Provider


class OpenRouterProvider(OpenAIProvider):
    @staticmethod
    def _get_data() -> Provider.Details:
        return {
            "type": Provider.Type.CLOUD,
            "api_key_env": "OPENROUTER_API_KEY",
            "model_env": "CODA_OPENROUTER_MODEL",
            "model_required": True,
            "default_base_url": "https://openrouter.ai/api/v1",
        }

    @staticmethod
    def _get_name() -> str:
        return "OpenRouter"

    def describe(self) -> str:
        return f"openrouter (model: {self.get_configured_model() or 'not configured'})"
