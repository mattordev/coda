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
    strategy: str | None = None
    error: str | None = None

    @property
    def matched(self) -> bool:
        """Return whether the router matched an intent."""
        return self.intent is not None
