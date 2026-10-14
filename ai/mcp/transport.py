"""Official MCP SDK boundary for bounded stdio and Streamable HTTP sessions."""

from contextlib import contextmanager, asynccontextmanager
import json
import logging
import os
from threading import Event, get_ident
import time
from collections.abc import Callable

import anyio
import httpx2
from mcp import Client, StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp import types
from mcp.shared.exceptions import MCPError

from ai.mcp.config import LocalServerDefinition
from ai.mcp.remote import RemoteServerDefinition
from ai.tools.models import ToolError, ToolResult


def failure(code: str, message: str) -> ToolResult:
    return ToolResult(success=False, error=ToolError(code=code, message=message))


def _is_sdk_timeout(error: BaseException) -> bool:
    """Recognize typed SDK timeouts without inspecting private error text."""
    if isinstance(error, BaseExceptionGroup):
        return any(_is_sdk_timeout(child) for child in error.exceptions)
    return isinstance(error, (TimeoutError, httpx2.TimeoutException)) or (
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
        if name.startswith(("mcp", "httpx", "httpcore")) and isinstance(logger, logging.Logger)
    ]
    for logger in loggers:
        logger.addFilter(guard)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(guard)


class _RemoteStatusError(Exception):
    def __init__(self, code):
        super().__init__("The remote MCP request failed.")
        self.code = code


def _remote_error_code(error):
    if isinstance(error, BaseExceptionGroup):
        return next((code for child in error.exceptions if (code := _remote_error_code(child))), None)
    return error.code if isinstance(error, _RemoteStatusError) else None


@asynccontextmanager
async def _client_context(definition):
    """Own HTTP connections or local subprocesses behind a common SDK boundary."""
    if isinstance(definition, RemoteServerDefinition):
        status_code = None

        async def inspect_response(response):
            nonlocal status_code
            if response.status_code in (401, 403):
                status_code = "authentication_error"
            elif response.status_code == 413:
                status_code = "output_limit"

        try:
            async with httpx2.AsyncClient(
                headers=definition.headers(), timeout=httpx2.Timeout(definition.timeout_seconds),
                event_hooks={"response": [inspect_response]}, trust_env=False,
            ) as http:
                async with Client(
                    streamable_http_client(definition.url, http_client=http, max_sse_event_size=definition.max_sse_event_bytes),
                    read_timeout_seconds=definition.timeout_seconds, input_required_max_rounds=0,
                ) as client:
                    yield client
        except Exception:
            if status_code is not None:
                raise _RemoteStatusError(status_code) from None
            raise
    else:
        parameters = StdioServerParameters(
            command=definition.command, args=list(definition.args),
            env=definition.environment(), cwd=definition.cwd,
        )
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with Client(stdio_client(parameters, errlog=errlog),
                              read_timeout_seconds=definition.timeout_seconds, input_required_max_rounds=0) as client:
                yield client


class SDKStdioTransport:
    """Own local or remote SDK contexts; retain the original compatibility name."""

    def __init__(self):
        from threading import Lock
        self._sessions = {}
        self._sessions_lock = Lock()

    def retire(self, server_id, *, timeout_seconds=10.0):
        with self._sessions_lock:
            session = self._sessions.get(server_id)
        if session is None:
            return ToolResult(success=True)
        session.stop.set()
        if not session.done.wait(timeout_seconds):
            return failure("cleanup_timeout", "The MCP server cleanup has not completed.")
        with self._sessions_lock:
            if self._sessions.get(server_id) is session:
                del self._sessions[server_id]
        return ToolResult(success=True)

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
        if definition.session_mode == "persistent":
            from ai.mcp.session import PersistentSession
            with self._sessions_lock:
                session = self._sessions.get(definition.name)
                if session is not None and session.definition != definition:
                    return failure("configuration_error", "Retire the MCP session before replacing configuration.")
                if session is None or session.done.is_set():
                    session = PersistentSession(definition)
                    self._sessions[definition.name] = session
                return_session = session
            return return_session.request(
                operation, tool_name=tool_name, arguments=arguments,
                cancel_event=cancel_event, retire_event=retire_event,
                on_connected=on_connected,
            )
        try:
            definition.headers() if isinstance(definition, RemoteServerDefinition) else definition.environment()
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

            async with anyio.create_task_group() as group:
                group.start_soon(watch, group.cancel_scope)
                # Server stderr can contain credentials; do not forward it.
                async with _client_context(definition) as client:
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
            remote_code = _remote_error_code(error)
            if remote_code:
                return failure(remote_code, "The remote MCP request was rejected.")
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
