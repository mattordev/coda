from ai.tools.validation import compile_input_schema

from .models import Intent, IntentParameter


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
        self._validate_metadata(intent)
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

    @staticmethod
    def _validate_metadata(intent: Intent) -> None:
        """Reject invalid capability metadata before changing registry state."""
        if not isinstance(intent, Intent):
            raise TypeError("Capability metadata must be an Intent.")
        for field_name in ("name", "description", "source"):
            value = getattr(intent, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Intent {field_name} cannot be empty or invalid.")
        if intent.source != intent.source.strip().lower():
            raise ValueError("Intent source must use lowercase without whitespace.")
        for field_name in ("aliases", "examples"):
            values = getattr(intent, field_name)
            if not isinstance(values, (tuple, list)) or any(
                not isinstance(value, str) for value in values
            ):
                raise ValueError(f"Intent {field_name} must contain strings.")
        if not isinstance(intent.parameters, (tuple, list)):
            raise ValueError("Intent parameters must be a sequence.")
        names: set[str] = set()
        for parameter in intent.parameters:
            if not isinstance(parameter, IntentParameter):
                raise ValueError("Intent parameters must be IntentParameter objects.")
            if not isinstance(parameter.name, str) or not parameter.name.strip():
                raise ValueError("Intent parameter name cannot be empty.")
            name = parameter.name.strip().lower()
            if name in names:
                raise ValueError("Intent contains duplicate parameter names.")
            if not isinstance(parameter.description, str):
                raise ValueError("Intent parameter description must be a string.")
            if not isinstance(parameter.required, bool):
                raise ValueError("Intent parameter required must be a boolean.")
            names.add(name)
        if intent.input_schema is not None:
            compile_input_schema(intent.input_schema)
        elif intent.source != "native":
            raise ValueError("External intent must declare an input schema.")

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
