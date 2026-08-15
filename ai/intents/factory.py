import os

from ai.providers import registry as provider_registry

from .local_classifier import LocalClassifierStrategy
from .router import (
    CommandPrefixStrategy,
    ExampleMatchStrategy,
    ExactMatchStrategy,
    IntentRouter,
)
from .registry import IntentRegistry


def _local_classifier_enabled() -> bool:
    value = os.getenv(
        "CODA_INTENT_LOCAL_CLASSIFIER",
        "1",
    ).strip().lower()

    return value in ("1", "true", "yes", "on")


def _split_provider_list(value: str) -> list[str]:
    return [
        provider.strip()
        for provider in value.split(",")
        if provider.strip()
    ]


def _get_local_classifier_provider():
    configured_names = _split_provider_list(
        os.getenv("CODA_LOCAL_PROVIDERS", "")
    )

    if configured_names:
        providers = provider_registry.get_configured_providers(
            configured_names,
            provider_type="local",
        )
    else:
        providers = provider_registry.get_providers_by_type("local")

    if not providers:
        return None

    provider_name = providers[0]
    return provider_registry.get_provider_module(provider_name)


def create_router(registry: IntentRegistry) -> IntentRouter:
    strategies = [
        ExactMatchStrategy(),
        ExampleMatchStrategy(),
        CommandPrefixStrategy(),
    ]

    if _local_classifier_enabled():
        provider = _get_local_classifier_provider()

        if provider is not None:
            strategies.append(
                LocalClassifierStrategy(provider.generate)
            )

    return IntentRouter(
        registry=registry,
        strategies=tuple(strategies),
    )