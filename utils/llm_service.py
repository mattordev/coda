import os

from ai.providers import registry

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


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
    if load_dotenv is not None:
        load_dotenv(override=True)

    _reset_conversation()

    registry.reload_providers()


def llm_fallback_enabled():
    configured_value = os.getenv("CODA_LLM_FALLBACK", "1")
    return configured_value.lower() in ("1", "true", "yes")


def _split_provider_list(value: str) -> list[str]:
    return [
        provider.strip()
        for provider in value.split(",")
        if provider.strip()
    ]


def _get_described_providers(env_name: str, provider_type: str) -> str:
    configured_names = _split_provider_list(os.getenv(env_name, ""))
    if configured_names:
        providers = registry.get_configured_providers(
            configured_names,
            provider_type=provider_type,
        )
    else:
        providers = registry.get_providers_by_type(provider_type)

    if not providers:
        return "none configured"

    return ", ".join(registry.describe_provider(provider) for provider in providers)


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
    cloud_providers = _get_described_providers("CODA_CLOUD_PROVIDERS", "cloud")
    local_providers = _get_described_providers("CODA_LOCAL_PROVIDERS", "local")
    return f"cloud: {cloud_providers}; local: {local_providers}"


def call_provider(provider_name, prompt):
    provider_module = registry.get_provider_module(provider_name)

    if provider_module is None:
        available = ", ".join(registry.SUPPORTED_PROVIDERS.keys())
        provider_name = registry.normalize_provider_name(provider_name)
        return None, f"Unknown provider: {provider_name}. Available: {available}"

    return _generate_provider_response(provider_module, prompt)


def get_provider_type(provider_name):
    return registry.get_provider_type(provider_name)


_reset_conversation()

