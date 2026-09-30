import os

from ai.providers.cancellable import CancellationScope, run_cancellable
from ai.providers.telemetry import ProviderMetricsCollector

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


def _generate_stream(client, model, messages, cancel_event, scope, collector=None):
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
    )
    close_stream = getattr(stream, "close", None)
    if callable(close_stream):
        close_stream = scope.add(close_stream)
    response_parts = []

    try:
        for chunk in stream:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Request cancelled.")

            if collector is not None:
                collector.observe_openai_chunk(chunk)

            if not chunk.choices:
                continue

            content = chunk.choices[0].delta.content
            if content:
                if collector is not None:
                    collector.observe_content(content)
                response_parts.append(content)
    finally:
        if callable(close_stream):
            close_stream()

    return "".join(response_parts)

def generate_with_metadata(messages, cancel_event=None):
    api_key = get_api_key()
    model = get_model()
    collector = ProviderMetricsCollector(model)
    
    if openai is None:
        return collector.finish(error="openai package is not installed.")
    
    if not api_key:
        return collector.finish(error="OPENAI_API_KEY is not set in env.")
    
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
                    collector,
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
        return collector.finish(error="Request cancelled.")
    except Exception as exc:
        return collector.finish(error=str(exc))

    return collector.finish(response=(assistant_message or "").strip())


def generate(messages, cancel_event=None):
    result = generate_with_metadata(messages, cancel_event=cancel_event)
    return result.response, result.error
