from __future__ import annotations

from enum import StrEnum
import json

import requests
from requests.exceptions import Timeout

from typing import TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Required, NotRequired
    from threading import Event

from ai.providers.cancellable import CancellationScope
from ai.providers.basellama import BaseLlamaProvider, LlamaModel
from ai.providers.provider import Provider


class _OllamaRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class _OllamaChatAPI(TypedDict):
    model: Required[str]
    messages: Required[list[_OllamaMessage]]
    stream: NotRequired[bool]


class _OllamaHttpResponse(TypedDict):
    models: Required[list[_OllamaModel]]


class _OllamaMessage(TypedDict):
    role: Required[_OllamaRole]
    content: Required[str]


class _OllamaModel(TypedDict):
    name: NotRequired[str]
    model: NotRequired[str]


class _OllamaChatChunk(TypedDict):
    model: str
    message: NotRequired[_OllamaMessage]
    error: NotRequired[str]
    response: NotRequired[str]
    done: bool


class OllamaProvider(BaseLlamaProvider):
    @staticmethod
    def _get_data() -> Provider.Details:
        return {
            "type": Provider.Type.LOCAL,
            "model_env": "CODA_OLLAMA_MODEL",
            "base_url_env": "CODA_OLLAMA_BASE_URL",
            "model_required": False,
            "default_model": "nemotron-3-nano:4b",
            "default_base_url": "http://localhost:11434",
        }

    @staticmethod
    def _get_name() -> str:
        return "Ollama"

    @staticmethod
    def preferred_models() -> list[str]:
        return ["nemotron-3-nano:4b"]

    @classmethod
    def get_models_url(cls) -> str:
        return f"{cls.get_base_url()}/api/tags"

    def _get_cold_start_timeout_seconds(self) -> float:
        return self._get_float_env("CODA_OLLAMA_COLD_START_TIMEOUT", 120.0)

    def _get_request(self, url: str) -> _OllamaHttpResponse | None:
        try:
            response = self.active_session.get(
                url, timeout=min(self._get_timeout_seconds(), 10)
            )
            response.raise_for_status()
        except Exception:
            return None

        http_response: _OllamaHttpResponse = response.json()

        return http_response

    def _get_models(
        self, url: str, *, raise_exception: bool = True
    ) -> list[LlamaModel]:
        models: list[LlamaModel] = []
        try:
            response = self._get_request(url)
            if response is not None:
                models = response.get("models", [])
        except Exception:
            if raise_exception:
                raise

        return models

    def describe(self) -> str:
        model, error = self.get_model()
        if error:
            return f"ollama (model resolution failed: {error})"

        return f"ollama (model: {model})"

    def is_model_loaded(self, model_name: str) -> bool:
        ps_url = f"{self.get_base_url()}/api/ps"
        models = self._get_models(ps_url, raise_exception=False)

        if not models:
            return False

        for model in models:
            loaded_name = model.get("name") or model.get("model")
            if loaded_name == model_name:
                return True

        return False

    def _generate_stream(
        self,
        scope: CancellationScope,
        cancel_event: Event | None,
        model_name: str,
        messages: list[_OllamaMessage],
        timeout_seconds: float,
    ) -> tuple[str | None, str | None]:
        chat_url = f"{self.get_base_url()}/api/chat"

        post_json: _OllamaChatAPI = {
            "model": model_name,
            "messages": messages,
            "stream": True,
        }

        with self.active_session.post(
            chat_url, json=post_json, timeout=timeout_seconds, stream=True
        ) as response:
            close_response = scope.add(response.close)
            response.raise_for_status()
            response_parts: list[str] = []

            line: str
            for line in response.iter_lines(decode_unicode=True):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Request cancelled.")

                if not line:
                    continue

                response_json: _OllamaChatChunk = json.loads(line)
                provider_error = response_json.get("error")
                if provider_error:
                    return None, provider_error

                message: _OllamaMessage | None = response_json.get("message")
                if message:
                    content = message.get("content") or response_json.get("response")
                    if content:
                        response_parts.append(content)

                if response_json.get("done"):
                    break

            close_response()
            return "".join(response_parts), None

    def _generate_request(
        self,
        scope: CancellationScope,
        cancel_event: Event | None,
        messages: list[_OllamaMessage],
    ) -> tuple[str | None, str | None]:
        model_name, error = self.get_model()
        if error or not model_name:
            return None, error

        model_loaded = self.is_model_loaded(model_name)
        timeout_seconds = (
            self._get_timeout_seconds()
            if model_loaded
            else self._get_cold_start_timeout_seconds()
        )
        if model_loaded:
            timeout_message = f"Ollama timed out after {timeout_seconds} seconds while using loaded model {model_name}."
        else:
            timeout_message = (
                f"Ollama timed out after {timeout_seconds} seconds while starting model {model_name}. "
                f"The model may still be loading; try again in a moment or increase CODA_OLLAMA_COLD_START_TIMEOUT."
            )

        try:
            result = self._generate_stream(
                scope,
                cancel_event,
                model_name,
                messages,
                timeout_seconds,
            )
        except InterruptedError:
            return None, "Request cancelled."
        except Timeout:
            return None, timeout_message
        except Exception as exc:
            return None, str(exc)

        assistant_message, provider_error = result
        if provider_error:
            return None, provider_error

        return (assistant_message or "").strip(), None

    def generate(
        self, messages: list[_OllamaMessage], cancel_event: Event | None = None
    ) -> tuple[str | None, str | None] | None:
        session = requests.Session()

        timeout_seconds = max(
            self._get_timeout_seconds(), self._get_cold_start_timeout_seconds()
        )
        timeout_seconds += min(self._get_timeout_seconds(), 10) * 2

        try:
            return self._generate_request_wrapper(
                self._generate_request,
                session.close,
                timeout_seconds,
                messages,
            )
        except InterruptedError:
            return None, "Request cancelled."
        except Exception as exc:
            return None, str(exc)
