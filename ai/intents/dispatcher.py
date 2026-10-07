from collections.abc import Mapping
from typing import Protocol

from ai.tools.models import ToolError, ToolResult
from ai.tools.native import NativeCommandAdapter
from ai.tools.protocols import ToolExecutor

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
        self._executors: dict[str, ToolExecutor] = {}

        for name, command in commands.items():
            self.register(name, NativeCommandAdapter(command))

    def dispatch(self, request: IntentRequest) -> bool:
        """Execute the selected command and return whether it succeeded."""
        return self.execute(request).success

    def execute(self, request: IntentRequest) -> ToolResult:
        """Execute the selected command and return a structured outcome."""
        executor = self._executors.get(request.intent.name)

        if executor is None:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="tool_not_found",
                    message="The requested tool is not available.",
                ),
            )

        try:
            result = executor.execute(request)
        except Exception:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="execution_error",
                    message="The tool could not complete.",
                ),
            )

        if not isinstance(result, ToolResult):
            return ToolResult(
                success=False,
                error=ToolError(
                    code="invalid_tool_result",
                    message="The tool returned an invalid result.",
                ),
            )

        return result

    def register(self, name: str, executor: ToolExecutor) -> None:
        """Register an executor under its capability name."""
        if not isinstance(name, str):
            raise TypeError("Tool name must be a string.")

        if not name.strip():
            raise ValueError("Tool name cannot be empty.")

        if name in self._executors:
            raise ValueError(f"Tool '{name}' is already registered.")

        if not callable(getattr(executor, "execute", None)):
            raise TypeError("Tool executor must expose a callable execute().")

        self._executors[name] = executor
