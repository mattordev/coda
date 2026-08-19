import os

from ai.providers.cancellable import CancellationScope, run_cancellable

try:
    import openai
except ImportError:
    openai = None


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_api_key():
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def get_model():
    return os.getenv("CODA_OPENROUTER_MODEL", "").strip()


def reload_config():
    pass


def describe():
    model = get_model()
    return f"openrouter (model: {model or 'not configured'})"


def _get_timeout_seconds():
    try:
        return max(
            0.1,
            float(os.getenv("CODA_LLM_TIMEOUT", "45")),
        )
    except ValueError:
        return 45.0


def _generate_stream(
    client,
    model,
    messages,
    cancel_event,
    scope,
):
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
    )

    close_stream = getattr(stream, "close", None)

    if callable(close_stream):
        close_stream = scope.add(close_stream)

    response_parts = []

    try:
        for chunk in stream:
            if (
                cancel_event is not None
                and cancel_event.is_set()
            ):
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


def generate(messages, cancel_event=None):
    api_key = get_api_key()
    model = get_model()

    if openai is None:
        return None, "openai package is not installed."

    if not api_key:
        return None, "OPENROUTER_API_KEY is not set in env."

    if not model:
        return None, "CODA_OPENROUTER_MODEL is not set in env."

    try:
        client = openai.OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
        )

        scope = CancellationScope()

        close_client = getattr(client, "close", None)

        if callable(close_client):
            close_client = scope.add(close_client)

        try:
            assistant_message = run_cancellable(
                lambda: _generate_stream(
                    client,
                    model,
                    messages,
                    cancel_event,
                    scope,
                ),
                cancel_event,
                _get_timeout_seconds(),
                "OpenRouter generation timed out.",
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