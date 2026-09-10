from __future__ import annotations

import json
from threading import Event
import requests

from typing import TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Required

from ai.providers.basellama import BaseLlamaProvider, LlamaType
from . import Provider, ProviderError

if TYPE_CHECKING:

    class _Model(TypedDict):
        id: Required[str]

    class _HttpResponse(TypedDict):
        data: Required[list[_Model]]

    class _ChoiceData(TypedDict):
        delta: LlamaType.Message

    class _StreamData(TypedDict):
        choices: list[_ChoiceData]


class LlamacppProvider(BaseLlamaProvider):
    @staticmethod
    def _get_data() -> Provider.Details:
        return {
            "type": Provider.Type.LOCAL,
            "model_env": "CODA_LLAMACPP_MODEL",
            "base_url_env": "CODA_LLAMACPP_BASE_URL",
            "model_required": False,
            "default_base_url": "http://localhost:8080",
        }

    @staticmethod
    def _get_name() -> str:
        return "Llama.cpp"

    @staticmethod
    def _model_to_llama_model(model: _Model) -> LlamaType.Model:
        model_id = model.get("id")

        return LlamaType.Model(name=model_id, model=model_id)

    @classmethod
    def _get_models_url(cls) -> str:
        return f"{cls.get_base_url()}/v1/models"

    @classmethod
    def _get_chat_url(cls) -> str:
        return f"{cls.get_base_url()}/v1/chat/completions"

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
                _models = response.get("data", [])
                # here we want to translate a group of _Model to LlamaType.Model
                models = [self._model_to_llama_model(_model) for _model in _models]
        except Exception:
            if raise_exception:
                raise

        return models

    def _process_response(
        self, response: requests.Response, cancel_event: Event | None = None
    ) -> list[str]:
        DATA_PREFIX = "data: "
        TERMINATION_STR = "[DONE]"

        response_parts: list[str] = []

        for line in response.iter_lines(decode_unicode=True):
            if cancel_event is not None and cancel_event.is_set():
                raise ProviderError.Cancellation("Request cancelled.")

            if not isinstance(line, str):
                continue

            if not line:
                continue

            line = line.removeprefix(DATA_PREFIX)

            if line == TERMINATION_STR:
                break

            response_json: _StreamData = json.loads(line)

            choices = response_json.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            content = delta.get("content")

            if content:
                response_parts.append(content)

        return response_parts
