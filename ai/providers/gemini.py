import os

from ai.providers.cancellable import CancellationScope, run_cancellable

try:
    import openai
except ImportError:
    openai = None


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def get_api_key():
    return os.getenv("GEMINI_API_KEY", "").strip()


def get_model():
    return (
        os.getenv("CODA_GEMINI_MODEL", "gemini-3.7-flash").strip()
        or "gemini-3.7-flash"
    )


def reload_config():
    pass


def describe():
    return f"gemini (model: {get_model()})"


def _get_timeout_seconds():
    try:
        return max(0.1, float(os.getenv("CODA_LLM_TIMEOUT", "45")))
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
        reasoning_effort="none",
    )

    close_stream = getattr(stream, "close", None)

    if callable(close_stream):
        close_stream = scope.add(close_stream)

    response_parts = []

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


def generate(messages, cancel_event=None):
    api_key = get_api_key()
    model = get_model()

    if openai is None:
        return None, "openai package is not installed."

    if not api_key:
        return None, "GEMINI_API_KEY is not set in env."

    try:
        client = openai.OpenAI(
            api_key=api_key,
            base_url=GEMINI_BASE_URL,
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
                "Gemini generation timed out.",
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
