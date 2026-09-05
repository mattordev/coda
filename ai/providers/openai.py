from abc import abstractmethod
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
        }
    
    @staticmethod
    def _get_timeout_message() -> str:
        return "OpenAI generation timed out."

    @classmethod
    def reload_config(cls) -> None:
        openai.api_key = cls.get_api_key() or None
            
    @classmethod
    def describe(cls) -> str:
        return f"openai (model: {cls.get_model()})"
        
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

    @classmethod
    def generate(cls, messages: Iterable[ChatCompletionMessageParam], cancel_event: Event|None = None) -> tuple[str|None, str|None]:
        api_key = cls.get_api_key()
        model = cls.get_model()
        
        if openai is None:
            return None, "openai package is not installed."
        
        if not api_key:
            return None, f"{cls.instance().data.get("api_key_env")} is not set in env."

        # if we require a model, but haven't specified one, we cannot continue
        if not model and cls.instance().data.get("model_required"):
            return None, f"{cls.instance().data.get("model_env")} is not set in env."
            
        try:
            client = openai.OpenAI(api_key=api_key, base_url=cls.get_base_url())
            scope = CancellationScope()
            close_client = client.close
            if callable(close_client):
                close_client = scope.add(close_client)
            try:
                assistant_message = run_cancellable(
                    lambda: cls._generate_stream(
                        client,
                        model,
                        messages,
                        cancel_event,
                        scope,
                    ),
                    cancel_event,
                    cls._get_timeout_seconds(),
                    cls._get_timeout_message(),
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
