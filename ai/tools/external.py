"""Schema-validated execution independent of any external SDK or transport."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ai.tools.models import ToolError, ToolResult
from ai.tools.validation import compile_input_schema, validate_arguments

if TYPE_CHECKING:
    from ai.intents.models import Intent, IntentRequest


class ExternalToolAdapter:
    """Validate structured arguments before invoking an integration callback."""

    def __init__(
        self,
        intent: Intent,
        invoke: Callable[[dict[str, object]], ToolResult],
        *,
        preprocess: Callable[[IntentRequest], dict[str, object]] | None = None,
    ) -> None:
        if (
            not isinstance(intent.source, str)
            or not intent.source.strip()
            or intent.source.strip().lower() == "native"
        ):
            raise ValueError("An external tool must declare an external source.")
        if intent.input_schema is None:
            raise ValueError("An external tool must declare an input schema.")
        if not callable(invoke):
            raise TypeError("External tool invocation must be callable.")
        if preprocess is not None and not callable(preprocess):
            raise TypeError("External tool preprocessing must be callable.")
        if not isinstance(intent.name, str) or not intent.name.strip():
            raise ValueError("An external tool must declare a non-empty name.")
        self._name = intent.name.strip().lower()
        self._validator = compile_input_schema(intent.input_schema)
        self._invoke = invoke
        self._preprocess = preprocess

    def execute(self, request: IntentRequest) -> ToolResult:
        """Validate once and invoke once, without automatic retry or fallback."""
        if request.intent.name.strip().lower() != self._name:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="invalid_request",
                    message="The request does not match the selected tool.",
                ),
            )

        try:
            arguments = (
                self._preprocess(request)
                if self._preprocess is not None
                else request.arguments
            )
            arguments = validate_arguments(self._validator, arguments)
        except Exception:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="invalid_arguments",
                    message="The tool arguments could not be validated.",
                ),
            )

        try:
            result = self._invoke(arguments)
        except Exception:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="execution_error",
                    message="The external tool could not complete.",
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
