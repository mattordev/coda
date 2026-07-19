import os

from ai.providers import registry
from ai.privacy.sanitizer import sanitize_text

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

conversation_log = []

CLOUD_REDACTION_THRESHOLD = 0.3


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

def _get_cloud_safe_content(role, content, risk, privacy_result=None):
    if risk <= CLOUD_REDACTION_THRESHOLD:
        return content
    
    if privacy_result is not None and risk <= 0.7:
        sanitized = sanitize_text(content, privacy_result)
        if sanitized != content:
            return sanitized
    
    if role == "user":
        return "[Sensitive user request redacted for cloud provider.]"

    if role == "assistant":
        return "[Sensitive assistant response redacted for cloud provider.]"

    return content
        

def _new_message(role, content, risk=0.0, cloud_content=None, privacy_result=None):
    if cloud_content is None:
        cloud_content = _get_cloud_safe_content(role, content, risk, privacy_result)

    return {
        "role": role,
        "content": content,
        "risk": risk,
        "cloud_content": cloud_content,
    }
    
def _new_system_message():
    return _new_message(
        "system",
        _get_system_prompt(),
        risk=0.0,
    )

def _reset_conversation():
    global conversation_log

    conversation_log = [
        _new_system_message(),
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
    for m in conversation_log:
        if m["role"] == "system":
            system_messages.append(m)
        else:
            non_system.append(m)
    if len(non_system) > max_messages:
        conversation_log[:] = system_messages + non_system[-max_messages:]
        

def _build_messages_for_provider(provider_name):
    provider_type = registry.get_provider_type(provider_name)

    messages = []
    for entry in conversation_log:
        content = entry["content"]
        if provider_type == "cloud":
            content = entry["cloud_content"]

        messages.append({
            "role": entry["role"],
            "content": content,
        })

    return messages

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


def _generate_provider_response(provider_name, provider_module, user_text, risk=0.0, privacy_result=None):
    conversation_log.append(
    _new_message(
        "user",
        user_text,
        risk=risk,
        privacy_result=privacy_result,
    )
)

    messages = _build_messages_for_provider(provider_name)
    response, error = provider_module.generate(messages)

    if error:
        conversation_log.pop()
        return None, error

    response = (response or "").strip()
    if response:
        conversation_log.append(_new_message("assistant", response, risk=risk))
        _trim_conversation()

    return response, None


def describe_llm_fallback():
    cloud_providers = _get_described_providers("CODA_CLOUD_PROVIDERS", "cloud")
    local_providers = _get_described_providers("CODA_LOCAL_PROVIDERS", "local")
    return f"cloud: {cloud_providers}; local: {local_providers}"


def call_provider(provider_name, prompt, risk=0.0, privacy_result=None):
    provider_name = registry.normalize_provider_name(provider_name)
    provider_module = registry.get_provider_module(provider_name)

    if provider_module is None:
        available = ", ".join(registry.SUPPORTED_PROVIDERS.keys())
        return None, f"Unknown provider: {provider_name}. Available: {available}"

    return _generate_provider_response(
        provider_name,
        provider_module,
        prompt,
        risk=risk,
        privacy_result=privacy_result,
    )


def get_provider_type(provider_name):
    return registry.get_provider_type(provider_name)


_reset_conversation()

