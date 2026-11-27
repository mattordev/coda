from __future__ import annotations

from typing import TYPE_CHECKING

from ai.tools.models import ToolError, ToolResult
from ai.tools.progress import report_tool

if TYPE_CHECKING:
    from ai.intents.dispatcher import IntentCommand
    from ai.intents.models import IntentRequest


class NativeCommandAdapter:
    def __init__(self, command: IntentCommand) -> None:
        if not callable(getattr(command, "run", None)):
            raise TypeError("Native command must expose a callable run().")
        self._command = command

    def execute(self, request: IntentRequest) -> ToolResult:
        """Execute a native command and normalize its outcomes."""
        try:
            report_tool('action', request.intent.name)
            success = bool(self._command.run(request))
        except Exception:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="execution_error",
                    message="The native command could not complete.",
                ),
            )

        result = ToolResult(success=success)
        if success:
            report_tool('completed', request.intent.name, result=result)
        return result
