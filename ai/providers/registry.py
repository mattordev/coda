import os

from .provider import Provider

_SUPPORTED_PROVIDERS: dict[str, Provider] = {}

try:
    from . import OpenAIProvider

    _SUPPORTED_PROVIDERS["openai"] = OpenAIProvider()
except ImportError:
    pass

try:
    from . import OpenRouterProvider

    _SUPPORTED_PROVIDERS["openrouter"] = OpenRouterProvider()
except ImportError:
    pass

try:
    from . import GeminiProvider

    _SUPPORTED_PROVIDERS["gemini"] = GeminiProvider()
except ImportError:
    pass

try:
    from . import GrokProvider

    _SUPPORTED_PROVIDERS["grok"] = GrokProvider()
except ImportError:
    pass

try:
    from . import OllamaProvider

    _SUPPORTED_PROVIDERS["ollama"] = OllamaProvider()
except ImportError:
    pass

try:
    from . import LlamacppProvider

    _SUPPORTED_PROVIDERS["llamacpp"] = LlamacppProvider()
except ImportError:
    pass

__SUPPORTED_PROVIDERS: dict[str, Provider] = {
    # "ollama": {
    #     "type": "local",
    #     "model_env": "CODA_OLLAMA_MODEL",
    #     "base_url_env": "CODA_OLLAMA_BASE_URL",
    #     "model_required": False,
    # },
    # "llamacpp": {
    #     "type": "local",
    #     "model_env": "CODA_LLAMACPP_MODEL",
    #     "base_url_env": "CODA_LLAMACPP_BASE_URL",
    #     "model_required": False,
    # },
}


def get_provider(provider_name: str) -> Provider | None:
    provider = provider_name.strip().casefold()

    return _SUPPORTED_PROVIDERS.get(provider)


def is_provider_supported(provider_name: str) -> bool:
    return get_provider(provider_name) is not None


def is_provider_configured(provider_name: str) -> bool:
    provider = get_provider(provider_name)

    if not provider:
        return False

    if provider.data.get("type") == "local":
        return True

    api_key_env = provider.data.get("api_key_env")

    if not api_key_env:
        return False

    api_key = os.getenv(api_key_env, "").strip()

    if not api_key:
        return False

    if provider.data.get("model_required"):
        model_env = provider.data.get("model_env")

        if not model_env:
            return False

        model = os.getenv(model_env, "").strip()

        if not model:
            return False

    return True


def get_provider_type(provider_name: str) -> str | None:
    provider = get_provider(provider_name)

    if provider is None:
        return None

    return provider.data.get("type")


def get_provider_api_key_env(provider_name: str) -> str | None:
    provider = get_provider(provider_name)

    if provider is None:
        return None

    return provider.data.get("api_key_env")


def get_provider_model_env(provider_name: str) -> str | None:
    provider = get_provider(provider_name)

    if provider is None:
        return None

    return provider.data.get("model_env")


def get_provider_base_url_env(provider_name: str) -> str | None:
    provider = get_provider(provider_name)

    if provider is None:
        return None

    return provider.data.get("base_url_env")


def describe_provider(provider_name: str) -> str:
    provider = get_provider(provider_name)

    if provider is None:
        return provider_name

    return provider.describe()


def reload_providers() -> None:
    for provider in _SUPPORTED_PROVIDERS.values():
        provider.reload_config()


def get_configured_providers(
    provider_names: list[str], provider_type: str | None = None
) -> list[str]:
    configured: list[str] = []

    for provider_name in provider_names:
        if not is_provider_supported(provider_name):
            print(f"[PROVIDERS] Unknown provider '{provider_name}' ignored.")
            continue

        if not is_provider_configured(provider_name):
            print(f"[PROVIDERS] {provider_name} is not configured, skipping.")
            continue

        actual_type = get_provider_type(provider_name)
        if provider_type is not None and actual_type != provider_type:
            print(
                f"[PROVIDERS] {provider_name} is a {actual_type} provider, not a {provider_type} provider; skipping."
            )
            continue

        configured.append(provider_name)

    return configured


def resolve_provider_list(value: str, provider_type: Provider.Type) -> list[str]:
    provider_names = [
        provider.strip() for provider in value.split(",") if provider.strip()
    ]

    if provider_names:
        return get_configured_providers(provider_names, provider_type=provider_type)

    return get_providers_by_type(provider_type)


def get_providers_by_type(provider_type: Provider.Type) -> list[str]:
    providers: list[str] = []

    for provider_name, provider in _SUPPORTED_PROVIDERS.items():
        if provider.data.get("type") == provider_type and is_provider_configured(
            provider_name
        ):
            providers.append(provider_name)

    return providers
