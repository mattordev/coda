from __future__ import annotations

import openai
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionChunk
    from typing import Iterable

from ai.providers.openai import OpenAIProvider


class GeminiProvider(OpenAIProvider):
    @staticmethod
    def _get_data() -> GeminiProvider.Details:
        return {
            "type": GeminiProvider.Type.CLOUD,
            "api_key_env": "GEMINI_API_KEY",
            "model_env": "CODA_GEMINI_MODEL",
            "model_required": False,
            "default_model": "gemini-3.7-flash",
            "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        }

    @staticmethod
    def _get_name() -> str:
        return "Gemini"

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
