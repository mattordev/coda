from .models import Intent


class IntentRegistry:
    def __init__(self):
        """Initialize an empty intent registry."""
        self._intents: dict[str, Intent] = {}
        self._aliases: dict[str, str] = {}
        self._examples: dict[str, str] = {}

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize a name for consistent registry lookup."""
        return name.strip().lower()

    @staticmethod
    def _normalize_example(example: str) -> str:
        """Normalize a natural language example for matching."""
        return " ".join(example.strip().lower().rstrip("?.!").split())

    def register(self, intent: Intent) -> None:
        """Add an intent and its aliases to the registry."""
        name = self._normalize_name(intent.name)

        if not name:
            raise ValueError("Intent name cannot be empty.")

        if (
            name in self._intents
            or name in self._aliases
            or name in self._examples
        ):
            raise ValueError(f"Intent '{name}' is already registered.")

        normalized_aliases = [
            self._normalize_name(alias)
            for alias in intent.aliases
        ]
        normalized_examples = [
            self._normalize_example(example)
            for example in intent.examples
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

            if (
                alias in self._intents
                or alias in self._aliases
                or alias in self._examples
            ):
                raise ValueError(f"Name '{alias}' is already registered.")

            seen_aliases.add(alias)

        seen_examples: set[str] = set()

        for example in normalized_examples:
            if not example:
                raise ValueError(
                    f"Intent '{name}' contains an empty example."
                )

            if example == name or example in normalized_aliases:
                raise ValueError(
                    f"Intent '{name}' contains a redundant example "
                    f"'{example}'."
                )

            if example in seen_examples:
                raise ValueError(
                    f"Intent '{name}' contains duplicate example "
                    f"'{example}'."
                )

            if (
                example in self._intents
                or example in self._aliases
                or example in self._examples
            ):
                raise ValueError(
                    f"Example '{example}' is already registered."
                )

            seen_examples.add(example)

        self._intents[name] = intent

        for alias in normalized_aliases:
            self._aliases[alias] = name

        for example in normalized_examples:
            self._examples[example] = name

    def get(self, name: str) -> Intent | None:
        """Return the intent registered under a canonical name or alias."""
        name = self._normalize_name(name)
        canonical_name = self._aliases.get(name, name)
        return self._intents.get(canonical_name)

    def get_by_example(self, example: str) -> Intent | None:
        """Return an intent registered for a natural-language example."""
        normalized_example = self._normalize_example(example)
        intent_name = self._examples.get(normalized_example)

        if intent_name is None:
            return None

        return self._intents.get(intent_name)

    def all(self) -> tuple[Intent, ...]:
        """Return all registered intents in registration order."""
        return tuple(self._intents.values())
