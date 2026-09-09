from __future__ import annotations

import openai
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionChunk
    from typing import Iterable

from ai.providers.openai import OpenAIProvider


class GrokProvider(OpenAIProvider):
    @staticmethod
    def _get_data() -> GrokProvider.Details:
        return {
            "type": GrokProvider.Type.CLOUD,
            "api_key_env": "XAI_API_KEY",
            "model_env": "CODA_GROK_MODEL",
            "model_required": False,
            "default_model": "grok-4.5",
            "default_base_url": "https://api.x.ai/v1",
        }

    @staticmethod
    def _get_name() -> str:
        return "Grok"

    @staticmethod
    def _create_chat_completion_stream(
        client: openai.OpenAI,
        model: str,
        messages: Iterable[ChatCompletionMessageParam],
    ) -> openai.Stream[ChatCompletionChunk]:
        return client.chat.completions.create(
            model=model, messages=messages, stream=True, reasoning_effort="none"
        )

    def reload_config(self) -> None:
        pass
