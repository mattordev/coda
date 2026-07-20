from .models import Intent

class IntentRegistry:
    def __init__(self):
        """Initialize an empty intent registry."""
        self._intents: dict[str, Intent] = {}
        self._aliases: dict[str, str] = {}

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize a name for consistent registry lookup."""
        return name.strip().lower()

    def register(self, intent: Intent) -> None:
        """Add an intent and its aliases to the registry."""
        name = self._normalize_name(intent.name)

        if not name:
            raise ValueError("Intent name cannot be empty.")

        if name in self._intents or name in self._aliases:
            raise ValueError(f"Intent '{name}' is already registered.")

        normalized_aliases = [
            self._normalize_name(alias)
            for alias in intent.aliases
        ]

        seen_aliases: set[str] = set()

        for alias in normalized_aliases:
            if not alias:
                raise ValueError(f"Intent '{name}' contains an empty alias.")

            if alias == name:
                raise ValueError(
                    f"Intent '{name}' cannot use its own name as an alias."
                )

            if alias in seen_aliases:
                raise ValueError(
                    f"Intent '{name}' contains duplicate alias '{alias}'."
                )

            if alias in self._intents or alias in self._aliases:
                raise ValueError(f"Name '{alias}' is already registered.")

            seen_aliases.add(alias)

        self._intents[name] = intent

        for alias in normalized_aliases:
            self._aliases[alias] = name

    def get(self, name: str) -> Intent | None:
        """Return the intent registered under a canonical name or alias."""
        name = self._normalize_name(name)
        canonical_name = self._aliases.get(name, name)
        return self._intents.get(canonical_name)

    def all(self) -> tuple[Intent, ...]:
        """Return all registered intents in registration order."""
        return tuple(self._intents.values())
