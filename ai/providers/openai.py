from __future__ import annotations

import openai
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from threading import Event
    from typing import Iterable
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionChunk

from ai.providers.cancellable import CancellationScope, CANCELLATION_MESSAGE
from ai.providers.provider import Provider


class OpenAIProvider(Provider):
    @staticmethod
    def _get_data() -> Provider.Details:
        return {
            "type": Provider.Type.CLOUD,
            "api_key_env": "OPENAI_API_KEY",
            "model_env": "CODA_OPENAI_MODEL",
            "model_required": False,
            "default_model": "gpt-4o-mini",
        }

    @staticmethod
    def _get_name() -> str:
        return "OpenAI"

    def reload_config(self) -> None:
        openai.api_key = self.get_api_key() or None

    def describe(self) -> str:
        return f"{self._get_name()} (model: {self.get_configured_model()})"

    @staticmethod
    def _create_chat_completion_stream(
        client: openai.OpenAI,
        model: str,
        messages: Iterable[ChatCompletionMessageParam],
    ) -> openai.Stream[ChatCompletionChunk]:
        return client.chat.completions.create(
            model=model, messages=messages, stream=True
        )

    @classmethod
    def _generate_stream(
        cls,
        scope: CancellationScope,
        cancel_event: Event | None,
        client: openai.OpenAI,
        model: str,
        messages: Iterable[ChatCompletionMessageParam],
    ) -> str:
        stream = cls._create_chat_completion_stream(client, model, messages)
        close_stream = scope.add(stream.close)
        response_parts: list[str] = []

        try:
            for chunk in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError(CANCELLATION_MESSAGE)

                if not chunk.choices:
                    continue

                content = chunk.choices[0].delta.content
                if content:
                    response_parts.append(content)
        finally:
            close_stream()

        return "".join(response_parts)

    def generate(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        cancel_event: Event | None = None,
    ) -> tuple[str | None, str | None]:
        api_key = self.get_api_key()
        model = self.get_configured_model()

        if openai is None:
            return None, "openai package is not installed."

        if not api_key:
            api_key_env = self.data.get("api_key_env")
            return None, f"{api_key_env} is not set in env."

        # if we require a model, but haven't specified one, we cannot continue
        if not model and self.data.get("model_required"):
            model_env = self.data.get("model_env")
            return None, f"{model_env} is not set in env."

        try:
            client = openai.OpenAI(api_key=api_key, base_url=self.get_base_url())

            assistant_message = self._generate_request_wrapper(
                self._generate_stream,
                client.close,
                self._get_timeout_seconds(),
                cancel_event,
                client,
                model,
                messages,
            )
        except InterruptedError:
            return None, "Request cancelled."
        except Exception as exc:
            return None, str(exc)

        return (assistant_message or "").strip(), None
