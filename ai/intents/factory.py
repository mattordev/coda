import os

from ai.providers import ollama as ollama_provider

from .local_classifier import LocalClassifierStrategy
from .router import (
    CommandPrefixStrategy,
    ExampleMatchStrategy,
    ExactMatchStrategy,
    IntentRouter,
)
from .registry import IntentRegistry


def _local_classifier_enabled() -> bool:
    """Return whether local intent classification is enabled."""
    value = os.getenv(
        "CODA_INTENT_LOCAL_CLASSIFIER",
        "1",
    ).strip().lower()

    return value in ("1", "true", "yes", "on")


def create_router(registry: IntentRegistry) -> IntentRouter:
    """Create CODA's router using the supplied intent registry."""
    strategies = [
        ExactMatchStrategy(),
        ExampleMatchStrategy(),
        CommandPrefixStrategy(),
    ]

    if _local_classifier_enabled():
        strategies.append(
            LocalClassifierStrategy(ollama_provider.generate)
        )

    return IntentRouter(
        registry=registry,
        strategies=tuple(strategies),
    )
