from __future__ import annotations

import json

import requests
from requests.exceptions import Timeout

from typing import TypedDict, TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Required, NotRequired

from ai.providers.cancellable import CancellationScope, run_cancellable
from ai.providers.provider import Provider

class _OllamaHttpResponse(TypedDict):
    models: Required[list[_OllamaModel]]

class _OllamaModel(TypedDict):
    name: NotRequired[str]
    model: NotRequired[str]

class OllamaProvider(Provider):
    @staticmethod
    def _get_data() -> Provider.Details:
        return {
            "type": Provider.Type.LOCAL,
            "model_env": "CODA_OLLAMA_MODEL",
            "base_url_env": "CODA_OLLAMA_BASE_URL",
            "model_required": False,
            "default_base_url": "http://localhost:11434",
            "default_model": "gpt-4o-mini"
        }

    @staticmethod
    def preferred_models() -> list[str]:
        return [
            "nemotron-3-nano:4b"
        ]

    def get_base_url(self) -> str | None:
        base_url = super().get_base_url()

        if not base_url:
            return None

        return base_url.rstrip("/")
    
    def get_cold_start_timeout_seconds(self) -> float:
        return self._get_float_env("CODA_OLLAMA_COLD_START_TIMEOUT", 120.0)
    
    def reload_config(self) -> None:
        self._model_cache = None

    def _get_response(self, url: str) -> _OllamaHttpResponse|None:
        try:
            response = requests.get(url, timeout=min(self._get_timeout_seconds(), 10))
            response.raise_for_status()
        except Exception:
            return None

        return _OllamaHttpResponse(**response.json())

    def _get_models(self, url: str, *, raise_exception: bool = True) -> list[_OllamaModel]:
        models: list[_OllamaModel] = []
        try:
            response = self._get_response(url)
            if response is not None:
                models = response.get("models", [])
        except Exception:
            if raise_exception:
                raise

        return models

    def get_model(self):
        configured_model = self.get_configured_model()
        if configured_model:
            return configured_model, None

        if self._model_cache:
            return self._model_cache, None

        model_env = self.data.get("model_env")
        models: list[_OllamaModel] = []
        try:
            tags_url = f"{self.get_base_url()}/api/tags"
            models = self._get_models(tags_url)
        except Exception as exc:
            return None, f"Could not fetch Ollama models automatically. Set {model_env} explicitly. Details: {exc}"

        if not models:
            return None, f"No Ollama models were found at the configured host. Set {model_env} after pulling a model on the host."

        model_names: list[str] = []
        for model in models:
            model_name = model.get("name") or model.get("model")
            if model_name:
                model_names.append(model_name)

        selected_model: str|None = None
        for preferred_model in self.preferred_models():
            if preferred_model in model_names:
                selected_model = preferred_model
                break

        if selected_model is None:
            selected_model = model_names[0] if model_names else None

        if not selected_model:
            return None, "Ollama returned models but none included a usable name."

        _model_cache = selected_model
        return _model_cache, None

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
    http_client,
    base_url,
    model,
    messages,
    timeout_seconds,
    cancel_event,
    scope,
):
    with http_client.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": True,
        },
        timeout=timeout_seconds,
        stream=True,
    ) as response:
        close_response = scope.add(response.close)
        response.raise_for_status()
        response_parts = []

        for line in response.iter_lines(decode_unicode=True):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Request cancelled.")

            if not line:
                continue

            response_json = json.loads(line)
            provider_error = response_json.get("error")
            if provider_error:
                return None, provider_error

            message = response_json.get("message", {})
            content = message.get("content") or response_json.get("response")
            if content:
                response_parts.append(content)

            if response_json.get("done"):
                break

        close_response()
        return "".join(response_parts), None


def _generate_request(messages, cancel_event, http_client, scope):
    model, error = get_model(http_client)
    if error:
        return None, error

    base_url = get_base_url()
    model_loaded = is_model_loaded(model, http_client)
    timeout_seconds = (
        get_timeout_seconds()
        if model_loaded
        else get_cold_start_timeout_seconds()
    )
    if model_loaded:
        timeout_message = (
            f"Ollama timed out after {timeout_seconds} seconds while using "
            f"loaded model {model}."
        )
    else:
        timeout_message = (
            f"Ollama timed out after {timeout_seconds} seconds while starting "
            f"model {model}. The model may still be loading; try again in a "
            "moment or increase CODA_OLLAMA_COLD_START_TIMEOUT."
        )

    try:
        result = _generate_stream(
            http_client,
            base_url,
            model,
            messages,
            timeout_seconds,
            cancel_event,
            scope,
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


def generate(messages, cancel_event=None):
    session = requests.Session()
    scope = CancellationScope()
    close_session = scope.add(session.close)
    timeout_seconds = (
        max(
            get_timeout_seconds(),
            get_cold_start_timeout_seconds(),
        )
        + (2 * min(get_timeout_seconds(), 10))
    )

    try:
        return run_cancellable(
            lambda: _generate_request(
                messages,
                cancel_event,
                session,
                scope,
            ),
            cancel_event,
            timeout_seconds,
            "Ollama request timed out.",
            on_abandon=scope.cancel,
        )
    except InterruptedError:
        return None, "Request cancelled."
    except Exception as exc:
        return None, str(exc)
    finally:
        close_session()
