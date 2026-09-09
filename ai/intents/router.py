from __future__ import annotations

from dataclasses import replace
from typing import Protocol, TYPE_CHECKING
from abc import abstractmethod

from .models import IntentResult
from .registry import IntentRegistry

if TYPE_CHECKING:
    from .models import Intent


class DetectionStrategy(Protocol):
    name: str

    @abstractmethod
    def detect(
        self,
        message: str,
        registry: IntentRegistry,
    ) -> IntentResult:
        """Attempt to detect an intent from a message."""


class ExactMatchStrategy(DetectionStrategy):
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
            confidence=1.0,
            strategy=self.name,
        )


class ExampleMatchStrategy(DetectionStrategy):
    name = "example_match"

    def detect(
        self,
        message: str,
        registry: IntentRegistry,
    ) -> IntentResult:
        """Match a complete message against a declared intent example."""
        intent = registry.get_by_example(message)

        if intent is None:
            return IntentResult(intent=None)

        return IntentResult(
            intent=intent,
            confidence=1.0,
            strategy=self.name,
        )


class CommandPrefixStrategy(DetectionStrategy):
    name = "command_prefix"

    def detect(
        self,
        message: str,
        registry: IntentRegistry,
    ) -> IntentResult:
        """Match a message beginning with an intent name or alias."""
        normalized_message = " ".join(
            message.strip().lower().split()
        )
        matches: list[tuple[int, Intent]] = []

        for intent in registry.all():
            triggers = (
                intent.name,
                *intent.aliases,
            )

            for trigger in triggers:
                normalized_trigger = " ".join(
                    trigger.strip().lower().split()
                )
                prefix = normalized_trigger + " "

                if normalized_message.startswith(prefix):
                    matches.append(
                        (len(normalized_trigger), intent)
                    )

        if not matches:
            return IntentResult(intent=None)

        _, intent = max(
            matches,
            key=lambda match: match[0],
        )

        return IntentResult(
            intent=intent,
            confidence=1.0,
            strategy=self.name,
        )


class IntentRouter:
    def __init__(
        self,
        registry: IntentRegistry,
        strategies: tuple[DetectionStrategy, ...] | None = None,
        acceptance_threshold: float = 0.75,
        clarification_threshold: float = 0.40,
    ) -> None:
        """Initialize the router with a registry and detection strategies."""
        if not 0.0 <= clarification_threshold <= acceptance_threshold <= 1.0:
            raise ValueError(
                "Confidence thresholds must satisfy "
                "0.0 <= clarification <= acceptance <= 1.0."
            )

        self._registry = registry
        self._acceptance_threshold = acceptance_threshold
        self._clarification_threshold = clarification_threshold

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

        best_candidate: IntentResult | None = None

        for strategy in self._strategies:
            try:
                result = strategy.detect(
                    normalized_message,
                    self._registry,
                )
            except Exception as exc:
                errors.append(f"{strategy.name}: {exc}")
                continue

            if not result.matched:
                continue

            if result.confidence >= self._acceptance_threshold:
                return replace(result, accepted=True)

            if result.confidence >= self._clarification_threshold:
                candidate = replace(result, accepted=False)

                if (
                    best_candidate is None
                    or candidate.confidence > best_candidate.confidence
                ):
                    best_candidate = candidate

        if best_candidate is not None:
            return best_candidate

        if errors:
            return IntentResult(
                intent=None,
                error="; ".join(errors),
            )

        return IntentResult(intent=None)
