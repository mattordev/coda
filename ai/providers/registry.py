SUPPORTED_PROVIDERS = {
    "openai": {
        "type": "cloud",
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "CODA_OPENAI_MODEL",
    },

    "ollama": {
        "type": "local",
        "api_key_env": None,
        "model_env": "CODA_OLLAMA_MODEL",
    }
}

import os

def is_provider_supported(provider: str) -> bool:
    return provider in SUPPORTED_PROVIDERS

def is_provider_configured(provider: str) -> bool:
    if provider not in SUPPORTED_PROVIDERS:
        return False
    
    config = SUPPORTED_PROVIDERS[provider]
    
    # Local providers don't require API keys
    if config["type"] == "local":
        return True
    
    api_key = os.getenv(config["api_key_env"])
    
    return bool (api_key)
    
def get_provider_type(provider: str) -> str | None:
    if provider not in SUPPORTED_PROVIDERS:
        return None
    
    return SUPPORTED_PROVIDERS[provider]["type"]

def get_configured_providers(provider_names: list[str]) -> list[str]:
    configured = []
    
    for provider in provider_names:
        provider = provider.strip().lower()
        
        if not is_provider_supported(provider):
            print(f"[PROVIDERS] Unknown provider '{provider}' ignored.")
            continue
        
        if not is_provider_configured(provider):
            print(f"[PROVIDERS] {provider} is not configured, skipping.")
            continue
        
        configured.append(provider)
    
    return configured