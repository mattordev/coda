"""Official MCP SDK boundary for bounded, operation-scoped stdio sessions."""

from contextlib import contextmanager
import json
import logging
import os
from threading import Event, get_ident
import time
from collections.abc import Callable

import anyio
from mcp import Client, StdioServerParameters, stdio_client
from mcp import types
from mcp.shared.exceptions import MCPError

from ai.mcp.config import LocalServerDefinition
from ai.tools.models import ToolError, ToolResult


def failure(code: str, message: str) -> ToolResult:
    return ToolResult(success=False, error=ToolError(code=code, message=message))


def _is_sdk_timeout(error: BaseException) -> bool:
    """Recognize typed SDK timeouts without inspecting private error text."""
    if isinstance(error, BaseExceptionGroup):
        return any(_is_sdk_timeout(child) for child in error.exceptions)
    return isinstance(error, TimeoutError) or (
        isinstance(error, MCPError) and error.code == types.REQUEST_TIMEOUT
    )


@contextmanager
def _private_sdk_diagnostics():
    """Suppress SDK raw exception diagnostics on this operation's thread."""
    thread_id = get_ident()

    class PrivateDiagnostics(logging.Filter):
        def filter(self, record):
            return record.thread != thread_id

    guard = PrivateDiagnostics()
    loggers = [
        logger for name, logger in list(logging.Logger.manager.loggerDict.items())
        if name.startswith("mcp") and isinstance(logger, logging.Logger)
    ]
    for logger in loggers:
        logger.addFilter(guard)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(guard)


class SDKStdioTransport:
    """Own one SDK session/process per operation; always unwind its context."""

    def request(
        self,
        definition: LocalServerDefinition,
        operation: str,
        *,
        tool_name: str | None = None,
        arguments: dict[str, object] | None = None,
        cancel_event: Event | None = None,
        retire_event: Event | None = None,
        on_connected: Callable[[], None] | None = None,
    ) -> ToolResult:
        if operation not in {"discover", "invoke"}:
            return failure("invalid_request", "The MCP operation is not supported.")
        if not definition.trusted:
            return failure("permission_denied", "The MCP process is not trusted.")
        if operation == "invoke" and tool_name not in definition.allowed_tools:
            return failure("permission_denied", "The MCP tool is not permitted.")
        if any(event is not None and event.is_set() for event in (cancel_event, retire_event)):
            return failure("cancelled", "The MCP operation was cancelled.")
        try:
            environment = definition.environment()
        except ValueError:
            return failure("configuration_error", "An MCP environment reference is unavailable.")
        reason = None
        connected = False
        outcome = None
        deadline = time.monotonic() + definition.startup_timeout_seconds

        async def perform():
            nonlocal reason, outcome, deadline, connected

            async def watch(scope):
                nonlocal reason
                while True:
                    if any(event is not None and event.is_set() for event in (cancel_event, retire_event)):
                        reason = "cancelled"
                    elif time.monotonic() >= deadline:
                        reason = "timeout"
                    if reason is not None:
                        scope.cancel()
                        return
                    await anyio.sleep(0.01)

            parameters = StdioServerParameters(
                command=definition.command, args=list(definition.args),
                env=environment, cwd=definition.cwd,
            )
            async with anyio.create_task_group() as group:
                group.start_soon(watch, group.cancel_scope)
                # Server stderr can contain credentials; do not forward it.
                with open(os.devnull, "w", encoding="utf-8") as errlog:
                    async with Client(
                        stdio_client(parameters, errlog=errlog),
                        read_timeout_seconds=definition.timeout_seconds,
                        input_required_max_rounds=0,
                    ) as client:
                        connected = True
                        deadline = time.monotonic() + definition.timeout_seconds
                        if on_connected is not None:
                            on_connected()
                        if operation == "discover":
                            tools = []
                            cursor = None
                            seen_cursors = set()
                            while True:
                                page = await client.list_tools(cursor=cursor)
                                tools.extend(tool.model_dump(mode="json", by_alias=True) for tool in page.tools)
                                if len(tools) > definition.max_tools:
                                    raise ValueError("MCP discovery exceeded the tool limit.")
                                cursor = page.next_cursor
                                if cursor is None:
                                    break
                                if cursor in seen_cursors:
                                    raise ValueError("MCP discovery repeated a cursor.")
                                seen_cursors.add(cursor)
                            outcome = {"tools": tools}
                        elif operation == "invoke":
                            response = await client.call_tool(tool_name, arguments)
                            outcome = response.model_dump(mode="json", by_alias=True)
                        else:
                            raise ValueError("Unknown MCP operation.")
                        # SDK teardown has its own bounded waits; the call is complete.
                        deadline = float("inf")
                group.cancel_scope.cancel()

        try:
            with _private_sdk_diagnostics():
                anyio.run(perform)
        except Exception as error:
            if reason is None and _is_sdk_timeout(error):
                reason = "timeout"
            if reason is None:
                return failure(
                    "mcp_protocol_error" if connected else "connection_error",
                    "The MCP server returned an invalid response or disconnected."
                    if connected else "The MCP server could not be started or connected.",
                )
        if reason is not None:
            return failure(
                reason,
                "The MCP operation was cancelled." if reason == "cancelled"
                else "The MCP operation timed out.",
            )
        if not isinstance(outcome, dict):
            return failure("mcp_protocol_error", "The MCP response was invalid.")
        try:
            output_bytes = json.dumps(outcome, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            return failure("mcp_protocol_error", "The MCP response was invalid.")
        if len(output_bytes) > definition.max_output_bytes:
            return failure("output_limit", "The MCP response exceeded the configured output limit.")
        if outcome.get("isError", False):
            return ToolResult(
                success=False, data=outcome,
                error=ToolError(code="mcp_tool_error", message="The MCP tool reported a failure."),
            )
        return ToolResult(success=True, data=outcome)
