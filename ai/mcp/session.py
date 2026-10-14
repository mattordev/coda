"""One owning thread/task keeps an SDK context alive across synchronous calls."""

from dataclasses import dataclass, field
import json
from queue import Empty, Queue
from threading import Event, Thread
import time

import anyio

from ai.mcp.transport import failure, _is_sdk_timeout, _private_sdk_diagnostics, _client_context, _remote_error_code
from ai.tools.models import ToolError, ToolResult


@dataclass
class _Operation:
    name: str
    options: dict
    done: Event = field(default_factory=Event)
    result: ToolResult | None = None


class PersistentSession:
    """Serialize calls while entering/exiting the SDK in the same async task."""

    def __init__(self, definition):
        self.definition = definition
        self.stop = Event()
        self.done = Event()
        self._queue = Queue()
        self._thread = None

    def request(self, operation, **options):
        job = _Operation(operation, options)
        self._queue.put(job)
        if self._thread is None:
            self._thread = Thread(target=self._run, name=f"mcp-{self.definition.name}", daemon=True)
            self._thread.start()
        while not job.done.wait(0.01):
            if self.done.is_set():
                return failure("connection_error", "The MCP session has closed; a later request may reconnect.")
        return job.result

    def _run(self):
        active = None
        connected = False
        reason = None
        deadline = time.monotonic() + self.definition.startup_timeout_seconds

        async def perform():
            nonlocal active, connected, reason, deadline

            async def watch(scope):
                nonlocal reason
                while True:
                    events = () if active is None else (
                        active.options.get("cancel_event"), active.options.get("retire_event"),
                    )
                    if self.stop.is_set() or any(e is not None and e.is_set() for e in events):
                        reason = "cancelled"
                    elif time.monotonic() >= deadline:
                        reason = "timeout"
                    if reason:
                        scope.cancel()
                        return
                    await anyio.sleep(0.01)

            active = self._queue.get_nowait()
            async with anyio.create_task_group() as group:
                group.start_soon(watch, group.cancel_scope)
                async with _client_context(self.definition) as client:
                    connected = True
                    while True:
                        deadline = time.monotonic() + self.definition.timeout_seconds
                        callback = active.options.get("on_connected")
                        if callback:
                            callback()
                        outcome = await self._execute(client, active)
                        active.result = self._normalize(outcome)
                        active.done.set()
                        active = None
                        idle = self.definition.idle_timeout_seconds
                        deadline = float("inf") if idle is None else time.monotonic() + idle
                        while active is None:
                            try:
                                active = self._queue.get_nowait()
                            except Empty:
                                await anyio.sleep(0.01)

        try:
            with _private_sdk_diagnostics():
                anyio.run(perform)
        except Exception as error:
            if _remote_error_code(error):
                reason = _remote_error_code(error)
            elif _is_sdk_timeout(error):
                reason = "timeout"
            elif not connected and isinstance(error, ValueError):
                reason = "configuration_error"
        finally:
            if active is not None and not active.done.is_set():
                code = reason or ("mcp_protocol_error" if connected else "connection_error")
                active.result = failure(code, "The MCP operation could not complete.")
                active.done.set()
            self.done.set()

    async def _execute(self, client, job):
        if job.name == "invoke":
            response = await client.call_tool(job.options["tool_name"], job.options["arguments"])
            return response.model_dump(mode="json", by_alias=True)
        tools, cursor, seen = [], None, set()
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(tool.model_dump(mode="json", by_alias=True) for tool in page.tools)
            if len(tools) > self.definition.max_tools:
                raise ValueError("Too many tools.")
            cursor = page.next_cursor
            if cursor is None:
                return {"tools": tools}
            if cursor in seen:
                raise ValueError("Repeated cursor.")
            seen.add(cursor)

    def _normalize(self, outcome):
        try:
            encoded = json.dumps(outcome, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            return failure("mcp_protocol_error", "The MCP response was invalid.")
        if len(encoded) > self.definition.max_output_bytes:
            return failure("output_limit", "The MCP response exceeded the configured output limit.")
        if outcome.get("isError", False):
            return ToolResult(success=False, data=outcome, error=ToolError(
                "mcp_tool_error", "The MCP tool reported a failure.",
            ))
        return ToolResult(success=True, data=outcome)
