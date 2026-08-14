import json
import os

import requests
from requests.exceptions import Timeout

from ai.providers.cancellable import CancellationScope, run_cancellable

_model_cache = None
_preferred_models = (
    "nemotron-3-nano:4b",
)


def _get_float_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def get_base_url():
    return os.getenv("CODA_OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def get_configured_model():
    return os.getenv("CODA_OLLAMA_MODEL", "").strip()


def get_timeout_seconds():
    return _get_float_env("CODA_LLM_TIMEOUT", 45.0)


def get_cold_start_timeout_seconds():
    return _get_float_env("CODA_OLLAMA_COLD_START_TIMEOUT", 120.0)


def reload_config():
    global _model_cache

    _model_cache = None


def get_model(http_client=requests):
    global _model_cache

    configured_model = get_configured_model()
    if configured_model:
        return configured_model, None

    if _model_cache:
        return _model_cache, None

    base_url = get_base_url()

    try:
        response = http_client.get(
            f"{base_url}/api/tags",
            timeout=min(get_timeout_seconds(), 10),
        )
        response.raise_for_status()
        models = response.json().get("models", [])
    except Exception as exc:
        return None, (
            "Could not fetch Ollama models automatically. "
            "Set CODA_OLLAMA_MODEL explicitly. "
            f"Details: {exc}"
        )

    if not models:
        return None, (
            "No Ollama models were found at the configured host. "
            "Set CODA_OLLAMA_MODEL after pulling a model on the host."
        )

    model_names = []
    for model in models:
        model_name = model.get("name") or model.get("model")
        if model_name:
            model_names.append(model_name)

    selected_model = None
    for preferred_model in _preferred_models:
        if preferred_model in model_names:
            selected_model = preferred_model
            break

    if selected_model is None:
        selected_model = model_names[0] if model_names else None

    if not selected_model:
        return None, "Ollama returned models but none included a usable name."

    _model_cache = selected_model
    return _model_cache, None


def is_model_loaded(model_name, http_client=requests):
    base_url = get_base_url()

    try:
        response = http_client.get(
            f"{base_url}/api/ps",
            timeout=min(get_timeout_seconds(), 10),
        )
        response.raise_for_status()
        models = response.json().get("models", [])
    except Exception:
        return False

    for model in models:
        loaded_name = model.get("name") or model.get("model")
        if loaded_name == model_name:
            return True

    return False


def describe():
    model, error = get_model()
    if error:
        return f"ollama (model resolution failed: {error})"

    return f"ollama (model: {model})"


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
        get_cold_start_timeout_seconds()
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
