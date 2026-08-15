import os

from ai.providers.cancellable import CancellationScope, run_cancellable

try:
    import openai
except ImportError:
    openai = None

def get_api_key():
    return os.getenv("OPENAI_API_KEY", "").strip()
    
def get_model():
    return os.getenv("CODA_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

def reload_config():
    api_key = get_api_key()
    
    if openai is not None and hasattr(openai, "api_key"):
        openai.api_key = api_key or None
        
def describe():
    return f"openai (model: {get_model()})"


def _get_timeout_seconds():
    try:
        return max(0.1, float(os.getenv("CODA_LLM_TIMEOUT", "45")))
    except ValueError:
        return 45.0


def _generate_stream(client, model, messages, cancel_event, scope):
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
        return None, "OPENAI_API_KEY is not set in env."
    
    if hasattr(openai, "api_key"):
        openai.api_key = api_key
        
    try:
        client = openai.OpenAI(api_key=api_key)
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
                "OpenAI generation timed out.",
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
