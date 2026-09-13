from __future__ import annotations

import json

import requests

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypedDict, Required, NotRequired
    from threading import Event

from ai.providers.basellama import BaseLlamaProvider, LlamaType
from . import Provider, ProviderError

if TYPE_CHECKING:

    class _HttpResponse(TypedDict):
        models: Required[list[_Model]]

    class _Model(TypedDict):
        name: NotRequired[str]
        model: NotRequired[str]


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
    def _get_models_url(cls) -> str:
        return f"{cls.get_base_url()}/api/tags"

    @classmethod
    def _get_chat_url(cls) -> str:
        return f"{cls.get_base_url()}/api/chat"

    @classmethod
    def _get_cold_start_timeout_seconds(cls) -> float:
        return cls._get_float_env("CODA_OLLAMA_COLD_START_TIMEOUT", 120.0)

    def _get_request(self, url: str) -> _HttpResponse | None:
        try:
            response = self.active_session.get(
                url, timeout=min(self._get_timeout_seconds(), 10)
            )
            response.raise_for_status()
        except Exception:
            return None

        http_response: _HttpResponse = response.json()

        return http_response

    def _get_models(
        self, url: str, *, raise_exception: bool = True
    ) -> list[LlamaType.Model]:
        models: list[LlamaType.Model] = []
        try:
            response = self._get_request(url)
            if response is not None:
                models = response.get("models", [])
        except Exception:
            if raise_exception:
                raise

        return models

    def _is_model_loaded(self, model_name: str) -> bool:
        ps_url = f"{self.get_base_url()}/api/ps"
        models = self._get_models(ps_url, raise_exception=False)

        if not models:
            return False

        for model in models:
            loaded_name = model.get("name") or model.get("model")
            if loaded_name == model_name:
                return True

        return False

    def _process_response(
        self, response: requests.Response, cancel_event: Event | None = None
    ) -> list[str]:
        response_parts: list[str] = []

        line: str
        for line in response.iter_lines(decode_unicode=True):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Request cancelled.")

            if not line:
                continue

            response_json: LlamaType.ChatChunk = json.loads(line)
            provider_error = response_json.get("error")
            if provider_error:
                raise ProviderError.Generic(provider_error)

            message: LlamaType.Message | None = response_json.get("message")
            if message:
                content = message.get("content") or response_json.get("response")
                if content:
                    response_parts.append(content)

            if response_json.get("done"):
                break

        return response_parts
