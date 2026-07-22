from dataclasses import dataclass


@dataclass(frozen=True)
class IntentParameter:
    name: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class Intent:
    name: str
    description: str
    parameters: tuple[IntentParameter, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentResult:
    intent: Intent | None
    confidence: float = 0.0
    strategy: str | None = None
    accepted: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        """Validate the intent result after initialization."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Confidence must be between 0.0 and 1.0.")

        if self.accepted and self.intent is None:
            raise ValueError("An accepted result must contain an intent.")

    @property
    def matched(self) -> bool:
        """Return whether a strategy found an intent candidate."""
        return self.intent is not None

    @property
    def needs_clarification(self) -> bool:
        """Return whether a candidate was found but not accepted."""
        return self.matched and not self.accepted
