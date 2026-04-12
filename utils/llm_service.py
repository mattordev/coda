import os

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    import openai
except ImportError:
    openai = None


if load_dotenv is not None:
    load_dotenv()


DEFAULT_SYSTEM_PROMPT = (
    "You are CODA, the user's personal voice assistant. "
    "Speak in a natural, warm, conversational way that sounds good when read aloud. "
    "Default to plain sentences, not markdown. "
    "Do not use tables, bullet lists, headings, code blocks, or heavy formatting unless the user explicitly asks for them. "
    "Keep most answers to two to four sentences, unless the user asks for depth. "
    "For broad or research-style questions, give a short spoken summary first, then offer to go deeper into one area. "
    "Be helpful and informative without sounding robotic, corporate, or overly formal."
)
conversation = []
ollama_model_cache = None
preferred_ollama_models = (
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


def _get_openai_api_key():
    return os.getenv("OPENAI_API_KEY", "").strip()


def _get_openai_model():
    return os.getenv("CODA_OPENAI_MODEL", "gpt-3.5-turbo").strip() or "gpt-3.5-turbo"


def _get_system_prompt():
    configured_prompt = os.getenv("CODA_SYSTEM_PROMPT", "").strip()
    if configured_prompt:
        return configured_prompt
    return DEFAULT_SYSTEM_PROMPT


def _reset_conversation():
    global conversation

    conversation = [
        {
            "role": "system",
            "content": _get_system_prompt(),
        },
    ]


def _get_llm_timeout_seconds():
    return _get_float_env("CODA_LLM_TIMEOUT", 45.0)


def reload_config():
    global ollama_model_cache

    if load_dotenv is not None:
        load_dotenv(override=True)

    ollama_model_cache = None
    _reset_conversation()

    openai_api_key = _get_openai_api_key()
    if openai is not None and hasattr(openai, "api_key"):
        openai.api_key = openai_api_key or None


def llm_fallback_enabled():
    configured_value = os.getenv("CODA_LLM_FALLBACK")
    if configured_value is None:
        configured_value = os.getenv("CODA_GPT_FALLBACK", "1")
    return configured_value.lower() in ("1", "true", "yes")


def _get_ollama_base_url():
    return os.getenv("CODA_OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _get_configured_ollama_model():
    return os.getenv("CODA_OLLAMA_MODEL", "").strip()


def _get_llm_provider():
    provider = os.getenv("CODA_LLM_PROVIDER", "").strip().lower()

    if provider == "gpt":
        return "openai"

    if provider in ("", "auto"):
        if _get_configured_ollama_model() or os.getenv("CODA_OLLAMA_BASE_URL"):
            return "ollama"
        return "openai"

    return provider


def _get_ollama_model():
    global ollama_model_cache

    configured_model = _get_configured_ollama_model()
    if configured_model:
        return configured_model, None

    if ollama_model_cache:
        return ollama_model_cache, None

    ollama_base_url = _get_ollama_base_url()

    try:
        response = requests.get(
            f"{ollama_base_url}/api/tags",
            timeout=min(_get_llm_timeout_seconds(), 10),
        )
        response.raise_for_status()
        models = response.json().get("models", [])
    except Exception as exc:
        return None, (
            "could not fetch Ollama models automatically. "
            "Set CODA_OLLAMA_MODEL explicitly. "
            f"Details: {exc}"
        )

    if not models:
        return None, (
            "no Ollama models were found at the configured host. "
            "Set CODA_OLLAMA_MODEL after pulling a model."
        )

    model_names = []
    for model in models:
        model_name = model.get("name") or model.get("model")
        if model_name:
            model_names.append(model_name)

    selected_model = None

    for preferred_model in preferred_ollama_models:
        if preferred_model in model_names:
            selected_model = preferred_model
            break

    if selected_model is None:
        selected_model = model_names[0] if model_names else None

    if not selected_model:
        return None, "Ollama returned models but none included a usable name"

    ollama_model_cache = selected_model
    return ollama_model_cache, None


def _generate_openai_response(user_text):
    openai_api_key = _get_openai_api_key()
    openai_model = _get_openai_model()

    if openai is None:
        return None, "openai package is not installed"

    if not openai_api_key:
        return None, "OPENAI_API_KEY is not set"

    if hasattr(openai, "api_key"):
        openai.api_key = openai_api_key

    conversation.append({"role": "user", "content": user_text})

    try:
        # openai>=1.x client API
        if hasattr(openai, "OpenAI"):
            client = openai.OpenAI(api_key=openai_api_key)
            response = client.chat.completions.create(
                model=openai_model,
                messages=conversation,
            )
            assistant_message = response.choices[0].message.content
        else:
            # openai<=0.x legacy API
            response = openai.ChatCompletion.create(
                model=openai_model,
                messages=conversation,
            )
            assistant_message = response["choices"][0]["message"]["content"]
    except Exception as exc:
        conversation.pop()
        return None, str(exc)

    assistant_message = (assistant_message or "").strip()
    if assistant_message:
        conversation.append({"role": "assistant", "content": assistant_message})

    return assistant_message, None


def _generate_ollama_response(user_text):
    model_name, error = _get_ollama_model()
    if error:
        return None, error

    ollama_base_url = _get_ollama_base_url()
    conversation.append({"role": "user", "content": user_text})

    try:
        response = requests.post(
            f"{ollama_base_url}/api/chat",
            json={
                "model": model_name,
                "messages": conversation,
                "stream": False,
            },
            timeout=_get_llm_timeout_seconds(),
        )
        response.raise_for_status()
        response_json = response.json()
        message = response_json.get("message", {})
        assistant_message = message.get("content") or response_json.get("response")
    except Exception as exc:
        conversation.pop()
        return None, str(exc)

    assistant_message = (assistant_message or "").strip()
    if assistant_message:
        conversation.append({"role": "assistant", "content": assistant_message})

    return assistant_message, None


def generate_llm_response(user_text):
    provider = _get_llm_provider()

    if provider == "openai":
        return _generate_openai_response(user_text)

    if provider == "ollama":
        return _generate_ollama_response(user_text)

    return None, (
        f"unsupported CODA_LLM_PROVIDER '{provider}'. "
        "Expected 'openai' or 'ollama'."
    )


def describe_llm_fallback():
    provider = _get_llm_provider()

    if provider == "openai":
        return f"openai (model: {_get_openai_model()})"

    if provider == "ollama":
        model_name, error = _get_ollama_model()
        if error:
            return f"ollama (model resolution failed: {error})"
        return f"ollama (model: {model_name})"

    return provider


def get_llm_provider():
    return _get_llm_provider()


_reset_conversation()
