import os

import requests

from ai.providers import openai as openai_provider
from ai.providers import ollama as ollama_provider

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()

PROVIDERS = {
    "openai": {
        "fn": lambda prompt: _generate_provider_response(openai_provider, prompt),
        "type": "cloud",
        "describe": openai_provider.describe,
    },
    "ollama": {
        "fn": lambda prompt: _generate_provider_response(ollama_provider, prompt),
        "type": "local",
        "describe": ollama_provider.describe,
    },
}

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


def _get_max_conversation_turns():
    value = os.getenv("CODA_CONVERSATION_MAX_TURNS")
    if value is None:
        return 20
    try:
        turns = int(value)
        return turns if turns > 0 else 20
    except ValueError:
        return 20


def _trim_conversation():
    """Keep the system prompt and at most the last max_turns user/assistant exchanges.

    Assumes each exchange is exactly one user message followed by one assistant
    message (which is how the callers always build the history).
    """
    max_turns = _get_max_conversation_turns()
    max_messages = max_turns * 2  # each turn = 1 user + 1 assistant message
    system_messages = []
    non_system = []
    for m in conversation:
        if m["role"] == "system":
            system_messages.append(m)
        else:
            non_system.append(m)
    if len(non_system) > max_messages:
        conversation[:] = system_messages + non_system[-max_messages:]


def reload_config():
    global ollama_model_cache

    if load_dotenv is not None:
        load_dotenv(override=True)

    ollama_model_cache = None
    _reset_conversation()
    
    openai_provider.reload_config()
    ollama_provider.reload_config()

def llm_fallback_enabled():
    configured_value = os.getenv("CODA_LLM_FALLBACK")
    if configured_value is None:
        configured_value = os.getenv("CODA_GPT_FALLBACK", "1")
    return configured_value.lower() in ("1", "true", "yes")

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

def _generate_provider_response(provider_module, user_text):
    conversation.append({"role": "user", "content": user_text})
    
    response, error = provider_module.generate(conversation)
    
    if error:
        conversation.pop()
        return None, error
    
    response = (response or "").strip()
    if response:
        conversation.append({"role": "assistant", "content": response})
        _trim_conversation()
        
    return response, None

def describe_llm_fallback():
    provider = _get_llm_provider()
    provider_info = PROVIDERS.get(provider)

    if not provider_info:
        return provider

    return provider_info["describe"]()


def get_llm_provider():
    return _get_llm_provider()

def call_provider(provider_name, prompt):
    provider_info = PROVIDERS.get(provider_name)

    if not provider_info:
        return None, f"Unknown provider: {provider_name}"

    return provider_info["fn"](prompt)


def get_provider_type(provider_name):
    provider_info = PROVIDERS.get(provider_name)
    if not provider_info:
        return None
    return provider_info.get("type")


_reset_conversation()
