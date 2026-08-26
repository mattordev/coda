from collections.abc import Callable, Mapping

import utils.runtime_state as runtime_state
from runtime.speech_playback import SpeechProvider


ProviderResolver = Callable[[], SpeechProvider | None]
ProviderResolvers = Mapping[str, ProviderResolver]


DEFAULT_PROVIDER_ORDER = (
    "elevenlabs",
    "pockettts",
    "pyttsx3",
)


def resolve_providers(
    provider_names: list[str],
    resolvers: ProviderResolvers,
) -> list[SpeechProvider]:
    providers = []

    for raw_name in provider_names:
        provider_name = normalize_provider_name(raw_name)
        resolver = resolvers.get(provider_name)

        if resolver is None:
            runtime_state.debug_print(
                f"[TTS] Unknown provider '{provider_name}' ignored."
            )
            continue

        try:
            provider = resolver()
        except Exception as error:
            runtime_state.debug_print(
                f"[TTS] Provider '{provider_name}' could not be resolved: "
                f"{error}. Skipping."
            )
            continue

        if provider is None:
            runtime_state.debug_print(
                f"[TTS] Provider '{provider_name}' is not configured; "
                "skipping."
            )
            continue

        providers.append(provider)

    return providers


def normalize_provider_name(provider_name: str) -> str:
    return (provider_name or "").strip().lower()


def parse_provider_order(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return list(DEFAULT_PROVIDER_ORDER)

    provider_names = []
    seen = set()

    for raw_name in value.split(","):
        provider_name = normalize_provider_name(raw_name)

        if not provider_name or provider_name in seen:
            continue

        seen.add(provider_name)
        provider_names.append(provider_name)

    return provider_names
