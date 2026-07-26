from collections.abc import Mapping
from typing import Protocol

from .models import IntentRequest


class IntentCommand(Protocol):
    def run(self, request: IntentRequest) -> bool:
        """Execute a structured request."""


class IntentDispatcher:
    def __init__(
        self,
        commands: Mapping[str, IntentCommand],
    ) -> None:
        """Initialize the dispatcher with commands keyed by the intent name."""
        self._commands = dict(commands)

    def dispatch(self, request: IntentRequest) -> bool:
        """Execute the command selected for an intent request."""
        command = self._commands.get(request.intent.name)

        if command is None:
            return False

        return bool(command.run(request))
