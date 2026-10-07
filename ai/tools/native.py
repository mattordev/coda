from ai.intents.dispatcher import IntentCommand
from ai.intents.models import IntentRequest
from ai.tools.models import ToolResult, ToolError


class NativeCommandAdapter:
    def __init__(self, command: IntentCommand) -> None:
        self._command = command


    def execute(self, request: IntentRequest) -> ToolResult:
        """Execute a native command and normalize its outcomes."""
        try:
            success = bool(self._command.run(request))
        except Exception:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="execution_error",
                    message="The native command could not complete.",
                ),
            )

        return ToolResult(success=success)