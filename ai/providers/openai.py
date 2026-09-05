from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from threading import Event
    from typing import Iterable

import openai
if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

from ai.providers.cancellable import CancellationScope, run_cancellable
from ai.providers.provider import Provider

class OpenAIProvider(Provider):
    @staticmethod
    def _get_data() -> Provider.Details:
        return {
            "type": Provider.Type.CLOUD,
            "api_key_env": "OPENAI_API_KEY",
            "model_env": "CODA_OPENAI_MODEL",
            "model_required": False,
            "default_model": "gpt-4o-mini"
        }
    
    @staticmethod
    def _get_timeout_message() -> str:
        return "OpenAI generation timed out."

    def reload_config(self) -> None:
        openai.api_key = self.get_api_key() or None
            
    def describe(self) -> str:
        return f"openai (model: {self.get_configured_model()})"
        
    @staticmethod
    def _generate_stream(client: openai.OpenAI, model: str, messages: Iterable[ChatCompletionMessageParam], cancel_event: Event|None, scope: CancellationScope) -> str:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        close_stream = stream.close
        if callable(close_stream):
            close_stream = scope.add(close_stream)
        response_parts: list[str] = []

        try:
            for chunk in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Request cancelled.")

                if not chunk.choices:
                    continue

                content = chunk.choices[0].delta.content
                if content:
                    response_parts.append(content)
        finally:
            if callable(close_stream):
                close_stream()

        return "".join(response_parts)

    def generate(self, messages: Iterable[ChatCompletionMessageParam], cancel_event: Event|None = None) -> tuple[str|None, str|None]:
        api_key = self.get_api_key()
        model = self.get_configured_model()
        
        if openai is None:
            return None, "openai package is not installed."
        
        if not api_key:
            return None, f"{self.data.get("api_key_env")} is not set in env."

        # if we require a model, but haven't specified one, we cannot continue
        if not model and self.data.get("model_required"):
            model_env = self.data.get("model_env")
            return None, f"{model_env} is not set in env."
            
        try:
            client = openai.OpenAI(api_key=api_key, base_url=self.get_base_url())
            scope = CancellationScope()
            close_client = client.close
            if callable(close_client):
                close_client = scope.add(close_client)
            try:
                assistant_message = run_cancellable(
                    lambda: self._generate_stream(
                        client,
                        model,
                        messages,
                        cancel_event,
                        scope,
                    ),
                    cancel_event,
                    self._get_timeout_seconds(),
                    self._get_timeout_message(),
                    on_abandon=scope.cancel,
                )
            finally:
                if callable(close_client):
                    close_client()
        except InterruptedError:
            return None, "Request cancelled."
        except Exception as exc:
            return None, str(exc)

        return (assistant_message or "").strip(), None
