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

def _get_local_classifier_provider():
    providers = provider_registry.resolve_provider_list(
        os.getenv("CODA_LOCAL_PROVIDERS", ""),
        provider_type="local",
    )

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