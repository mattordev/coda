from typing import Protocol

from .models import IntentResult
from .registry import IntentRegistry


class DetectionStrategy(Protocol):
    name: str

    def detect(
        self,
        message: str,
        registry: IntentRegistry,
    ) -> IntentResult:
        """Attempt to detect an intent from a message."""


class ExactMatchStrategy:
    name = "exact_match"

    def detect(
        self,
        message: str,
        registry: IntentRegistry,
    ) -> IntentResult:
        """Match a complete message against an intent name or alias."""
        intent = registry.get(message)

        if intent is None:
            return IntentResult(intent=None)

        return IntentResult(
            intent=intent,
            strategy=self.name,
        )


class IntentRouter:
    def __init__(
        self,
        registry: IntentRegistry,
        strategies: tuple[DetectionStrategy, ...] | None = None,
    ):
        """Initialize the router with a registry and detection strategies."""
        self._registry = registry

        if strategies is None:
            strategies = (ExactMatchStrategy(),)

        self._strategies = strategies

    def route(self, message: str) -> IntentResult:
        """Try each detection strategy until one matches an intent."""
        normalized_message = message.strip()

        if not normalized_message:
            return IntentResult(
                intent=None,
                error="Message cannot be empty.",
            )

        errors: list[str] = []

        for strategy in self._strategies:
            try:
                result = strategy.detect(
                    normalized_message,
                    self._registry,
                )
            except Exception as exc:
                errors.append(f"{strategy.name}: {exc}")
                continue

            if result.matched:
                return result

        if errors:
            return IntentResult(
                intent=None,
                error="; ".join(errors),
            )

        return IntentResult(intent=None)
