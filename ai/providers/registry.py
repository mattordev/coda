import os

from ai.providers import openai as openai_provider
from ai.providers import gemini as gemini_provider
from ai.providers import llamacpp as llamacpp_provider
from ai.providers import ollama as ollama_provider

SUPPORTED_PROVIDERS = {
    "openai": {
        "type": "cloud",
        "module": openai_provider,
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "CODA_OPENAI_MODEL",
        "base_url_env": None,
    },
    "gemini": {
        "type": "cloud",
        "module": gemini_provider,
        "api_key_env": "GEMINI_API_KEY",
        "model_env": "CODA_GEMINI_MODEL",
        "base_url_env": None,
    },
    "ollama": {
        "type": "local",
        "module": ollama_provider,
        "api_key_env": None,
        "model_env": "CODA_OLLAMA_MODEL",
        "base_url_env": "CODA_OLLAMA_BASE_URL",
    },
    "llamacpp": {
        "type": "local",
        "module": llamacpp_provider,
        "api_key_env": None,
        "model_env": "CODA_LLAMACPP_MODEL",
        "base_url_env": "CODA_LLAMACPP_BASE_URL",
    },
}

def normalize_provider_name(provider: str) -> str:
    return (provider or "").strip().lower()

def is_provider_supported(provider: str) -> bool:
    provider = normalize_provider_name(provider)
    return provider in SUPPORTED_PROVIDERS

def is_provider_configured(provider: str) -> bool:
    provider = normalize_provider_name(provider)

    if provider not in SUPPORTED_PROVIDERS:
        return False

    if get_provider_type(provider) == "local":
        return True

    api_key_env = get_provider_api_key_env(provider)

    if not api_key_env:
        return False

    api_key = os.getenv(api_key_env, "").strip()

    return bool(api_key)

def get_provider_type(provider: str) -> str | None:
    provider = normalize_provider_name(provider)

    if provider not in SUPPORTED_PROVIDERS:
        return None

    return SUPPORTED_PROVIDERS[provider]["type"]

def get_provider_api_key_env(provider: str) -> str | None:
    provider = normalize_provider_name(provider)

    if provider not in SUPPORTED_PROVIDERS:
        return None

    return SUPPORTED_PROVIDERS[provider]["api_key_env"]


def get_provider_model_env(provider: str) -> str | None:
    provider = normalize_provider_name(provider)

    if provider not in SUPPORTED_PROVIDERS:
        return None

    return SUPPORTED_PROVIDERS[provider]["model_env"]


def get_provider_base_url_env(provider: str) -> str | None:
    provider = normalize_provider_name(provider)

    if provider not in SUPPORTED_PROVIDERS:
        return None

    return SUPPORTED_PROVIDERS[provider]["base_url_env"]

def get_provider_module(provider: str):
    provider = normalize_provider_name(provider)

    if provider not in SUPPORTED_PROVIDERS:
        return None

    return SUPPORTED_PROVIDERS[provider]["module"]

def describe_provider(provider: str) -> str:
    provider_module = get_provider_module(provider)

    if provider_module is None:
        return normalize_provider_name(provider)

    return provider_module.describe()

def reload_providers():
    for config in SUPPORTED_PROVIDERS.values():
        provider_module = config["module"]
        provider_module.reload_config()

def get_configured_providers(
    provider_names: list[str],
    provider_type: str | None = None,
) -> list[str]:
    configured = []

    for provider in provider_names:
        provider = normalize_provider_name(provider)

        if not is_provider_supported(provider):
            print(f"[PROVIDERS] Unknown provider '{provider}' ignored.")
            continue

        if not is_provider_configured(provider):
            print(f"[PROVIDERS] {provider} is not configured, skipping.")
            continue

        actual_type = get_provider_type(provider)
        if provider_type is not None and actual_type != provider_type:
            print(
                f"[PROVIDERS] {provider} is a {actual_type} provider, "
                f"not a {provider_type} provider; skipping."
            )
            continue

        configured.append(provider)

    return configured

def resolve_provider_list(
    value: str,
    provider_type: str,
) -> list[str]:
    provider_names = [
        provider.strip()
        for provider in value.split(",")
        if provider.strip()
    ]
    
    if provider_names:
        return get_configured_providers(
            provider_names,
            provider_type=provider_type,
        )
        
    return get_providers_by_type(provider_type)

def get_providers_by_type(provider_type: str) -> list[str]:
    providers = []

    for provider_name, config in SUPPORTED_PROVIDERS.items():
        if config["type"] == provider_type and is_provider_configured(provider_name):
            providers.append(provider_name)

    return providers
