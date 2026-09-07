import json

import requests
from requests.exceptions import Timeout

from ai.providers.cancellable import CancellationScope
from ai.providers.basellama import BaseLlamaProvider
from ai.providers.provider import Provider

_model_cache = None


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

    @classmethod
    def get_models_url(cls) -> str:
        return f"{cls.get_base_url()}/v1/models"


def get_model(http_client=requests):
    global _model_cache

    configured_model = get_configured_model()
    if configured_model:
        return configured_model, None

    if _model_cache:
        return _model_cache, None

    try:
        response = http_client.get(
            f"{get_base_url()}/v1/models",
            timeout=min(get_timeout_seconds(), 10),
        )
        response.raise_for_status()
        models = response.json().get("data", [])
    except Exception as exc:
        return None, (
            "Could not fetch llama.cpp model information automatically. "
            "Set CODA_LLAMACPP_MODEL explicitly. "
            f"Details: {exc}"
        )

    if not models:
        return None, ("No model was reported by the configured llama.cpp server.")

    model = models[0].get("id")
    if not model:
        return None, "llama.cpp returned a model without a usable id."

    _model_cache = model
    return model, None


def describe():
    model, error = get_model()

    if error:
        return f"llamacpp (model resolution failed: {error})"

    return f"llamacpp (model: {model})"


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
        f"{base_url}/v1/chat/completions",
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

            if line.startswith("data: "):
                line = line[6:]

            if line == "[DONE]":
                break

            response_json = json.loads(line)

            choices = response_json.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            content = delta.get("content")

            if content:
                response_parts.append(content)

        close_response()
        return "".join(response_parts), None


def _generate_request(messages, cancel_event, http_client, scope):
    model, error = get_model(http_client)

    if error:
        return None, error

    timeout_seconds = get_timeout_seconds()

    try:
        result = _generate_stream(
            http_client,
            get_base_url(),
            model,
            messages,
            timeout_seconds,
            cancel_event,
            scope,
        )
    except InterruptedError:
        return None, "Request cancelled."
    except Timeout:
        return None, (f"llama.cpp timed out after {timeout_seconds} seconds.")
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

    timeout_seconds = get_timeout_seconds() + 10

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
            "llama.cpp request timed out.",
            on_abandon=scope.cancel,
        )
    except InterruptedError:
        return None, "Request cancelled."
    except Exception as exc:
        return None, str(exc)
    finally:
        close_session()
