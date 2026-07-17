import os

from ai.providers import ollama as ollama_provider
from ai.providers import openai as openai_provider

SUPPORTED_PROVIDERS = {
    "openai": {
        "type": "cloud",
        "aliases": ("gpt",),
        "module": openai_provider,
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "CODA_OPENAI_MODEL",
    },
    "ollama": {
        "type": "local",
        "aliases": (),
        "module": ollama_provider,
        "api_key_env": None,
        "model_env": "CODA_OLLAMA_MODEL",
    },
}

def normalize_provider_name(provider: str) -> str:
    provider = (provider or "").strip().lower()
    
    if provider in SUPPORTED_PROVIDERS:
        return provider
    
    for provider_name, config in SUPPORTED_PROVIDERS.items():
        if provider in config.get("aliases", ()):
            return provider_name
        
    return provider

def is_provider_supported(provider: str) -> bool:
    provider = normalize_provider_name(provider)
    return provider in SUPPORTED_PROVIDERS

def is_provider_configured(provider: str) -> bool:
    provider = normalize_provider_name(provider)
    
    if provider not in SUPPORTED_PROVIDERS:
        return False
    
    config = SUPPORTED_PROVIDERS[provider]
    
    # Local providers don't require API keys
    if config["type"] == "local":
        return True
    
    api_key = os.getenv(config["api_key_env"])
    
    return bool (api_key)
    
def get_provider_type(provider: str) -> str | None:
    provider = normalize_provider_name(provider)
    
    if provider not in SUPPORTED_PROVIDERS:
        return None
    
    return SUPPORTED_PROVIDERS[provider]["type"]

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

def get_configured_providers(provider_names: list[str]) -> list[str]:
    configured = []
    
    for provider in provider_names:
        provider = normalize_provider_name(provider)
        
        if not is_provider_supported(provider):
            print(f"[PROVIDERS] Unknown provider '{provider}' ignored.")
            continue
        
        if not is_provider_configured(provider):
            print(f"[PROVIDERS] {provider} is not configured, skipping.")
            continue
        
        configured.append(provider)
    
    return configured