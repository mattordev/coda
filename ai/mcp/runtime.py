"""Local MCP lifecycle and trusted, schema-validated tool executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from enum import Enum
import re
from threading import Event, Lock
import time
from collections.abc import Callable, Iterable

from ai.intents.models import Intent, IntentParameter, IntentRequest
from ai.intents.registry import IntentRegistry
from ai.mcp.config import LocalServerDefinition, discover_definitions
from ai.mcp.remote import RemoteServerDefinition
from ai.mcp.transport import SDKStdioTransport, failure
from ai.tools.external import ExternalToolAdapter
from ai.tools.models import ToolResult


class ServerState(str, Enum):
    CONFIGURED = "configured"
    STARTING = "starting"
    CONNECTED = "connected"
    AVAILABLE = "available"
    RETIRED = "retired"
    FAILED = "failed"


@dataclass
class _ServerRuntime:
    definition: LocalServerDefinition
    state: ServerState = ServerState.CONFIGURED
    lock: Lock = field(default_factory=Lock)
    retire_event: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    generation: int = 0
    retire_requests: int = 0
    configuration_revision: int = 0
    retry_after: float = 0.0


class LocalMCPManager:
    """Own independently configured servers without starting them at construction."""

    def __init__(
        self,
        definitions: Iterable[LocalServerDefinition],
        *,
        transport=None,
        permission_decider: Callable[[str, str, IntentRequest], str] | None = None,
    ):
        self._servers = {}
        self._tools: dict[str, tuple[str, str, Intent]] = {}
        self._metadata_lock = Lock()
        self._transport = transport or SDKStdioTransport()
        self._permission_decider = permission_decider
        self._closed = False
        for definition in definitions:
            if not isinstance(definition, (LocalServerDefinition, RemoteServerDefinition)):
                raise TypeError("MCP definitions must be CODA server definition objects.")
            if definition.name in self._servers:
                raise ValueError("MCP server id is duplicated.")
            runtime = _ServerRuntime(definition)
            runtime.done.set()
            self._servers[definition.name] = runtime

    @classmethod
    def from_directory(cls, directory=None, **kwargs):
        """Return a manager and per-file errors without launching any processes."""
        discovered = discover_definitions(directory)
        return cls(discovered.servers, **kwargs), discovered.errors

    def state(self, server_id: str) -> ServerState:
        with self._metadata_lock:
            return self._servers[server_id].state

    def inspect(self):
        """Return safe status dimensions without credentials or permission paths."""
        with self._metadata_lock:
            return {name: {
                "bundle": getattr(runtime.definition, "bundled_server", None),
                "enabled": getattr(runtime.definition, "enabled", True),
                "trusted": runtime.definition.trusted,
                "availability": "error" if getattr(runtime.definition, "bundle_error", None) else "available",
                "error": getattr(runtime.definition, "bundle_error", None),
                "connection": runtime.state.value,
            } for name, runtime in self._servers.items()}

    def _request(self, server_id, operation, *, cancel_event=None, configuration_revision=None, **kwargs):
        runtime = self._servers.get(server_id)
        if runtime is None:
            return failure("server_not_found", "The MCP server is not configured.")
        if not getattr(runtime.definition, "enabled", True):
            return failure("server_disabled", "The bundled MCP server is disabled; explicitly enable it in its definition.")
        if getattr(runtime.definition, "bundle_error", None):
            return failure("configuration_error", runtime.definition.bundle_error)
        if not runtime.definition.trusted:
            return failure("permission_denied", "The MCP process is not trusted.")
        with self._metadata_lock:
            if self._closed:
                return failure("cancelled", "The MCP manager has shut down.")
            if runtime.retire_requests:
                return failure("cancelled", "The MCP server is being retired.")
            generation = runtime.generation
            # Queued first-use callers share a cold launch. Its startup budget
            # must not be cut short by the shorter normal-action/busy timeout.
            startup_wait = (
                0 if runtime.state == ServerState.CONNECTED
                else runtime.definition.startup_timeout_seconds
            )
        deadline = time.monotonic() + startup_wait + runtime.definition.timeout_seconds
        while not runtime.lock.acquire(timeout=0.01):
            if cancel_event is not None and cancel_event.is_set():
                return failure("cancelled", "The MCP operation was cancelled.")
            if time.monotonic() >= deadline:
                return failure("timeout", "The MCP server is busy.")
        completed = False
        try:
            if cancel_event is not None and cancel_event.is_set():
                return failure("cancelled", "The MCP operation was cancelled.")
            with self._metadata_lock:
                if configuration_revision is not None and configuration_revision != runtime.configuration_revision:
                    return failure("tool_not_found", "The discovered MCP tool is no longer available.")
                if runtime.retire_requests or generation != runtime.generation:
                    return failure("cancelled", "The MCP operation was retired.")
                if time.monotonic() < runtime.retry_after:
                    return failure("connection_backoff", "The MCP server is waiting before reconnecting.")
                runtime.retire_event.clear()
                runtime.done.clear()
                runtime.state = ServerState.STARTING

            def connected():
                with self._metadata_lock:
                    runtime.state = ServerState.CONNECTED

            try:
                result = self._transport.request(
                    runtime.definition, operation, cancel_event=cancel_event,
                    retire_event=runtime.retire_event, on_connected=connected, **kwargs,
                )
                if not isinstance(result, ToolResult):
                    result = failure("mcp_protocol_error", "The MCP response was invalid.")
            except Exception as error:
                result = failure(
                    "mcp_internal_error", "An unexpected error prevented the MCP operation from completing.",
                    exception_type=type(error).__name__,
                )
            with self._metadata_lock:
                if runtime.retire_requests or generation != runtime.generation or (
                    cancel_event is not None and cancel_event.is_set()
                ):
                    result = failure("cancelled", "The MCP operation was retired.")
                runtime.state = (
                    ServerState.CONNECTED if result.success and runtime.definition.session_mode == "persistent"
                    else ServerState.RETIRED if result.success else ServerState.FAILED
                )
                if result.error is not None and result.error.code in ("connection_error", "mcp_protocol_error", "timeout", "authentication_error"):
                    runtime.retry_after = time.monotonic() + getattr(runtime.definition, "reconnect_backoff_seconds", 0)
                elif result.success:
                    runtime.retry_after = 0
                completed = True
            return result
        finally:
            with self._metadata_lock:
                if not completed and runtime.state in (ServerState.STARTING, ServerState.CONNECTED):
                    runtime.state = ServerState.FAILED
            runtime.done.set()
            runtime.lock.release()

    def discover(self, server_id: str, *, cancel_event: Event | None = None) -> ToolResult:
        """Fetch and validate an atomic capability catalog for one trusted server."""
        with self._metadata_lock:
            runtime = self._servers.get(server_id)
            generation = runtime.generation if runtime is not None else None
        result = self._request(server_id, "discover", cancel_event=cancel_event)
        if not result.success:
            return result
        try:
            definitions = result.data["tools"]
            if not isinstance(definitions, list):
                raise ValueError("Invalid MCP tool catalog.")
            if len(definitions) > self._servers[server_id].definition.max_tools:
                raise ValueError("MCP discovery exceeded the tool limit.")
            check_registry = IntentRegistry()
            pending = {}
            for definition in definitions:
                name = definition["name"]
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
                    raise ValueError("Invalid MCP tool name.")
                schema = definition["inputSchema"]
                properties = schema.get("properties", {})
                required = schema.get("required", [])
                parameters = tuple(
                    IntentParameter(
                        name=parameter_name,
                        description=parameter.get("description", "") if isinstance(parameter, dict) else "",
                        required=parameter_name in required,
                    )
                    for parameter_name, parameter in properties.items()
                )
                intent = Intent(
                    name=f"mcp.{server_id}.{name.lower()}",
                    description=definition.get("description") or f"Run {name} on {server_id}.",
                    parameters=parameters, source="mcp", input_schema=schema,
                )
                check_registry.register(intent)
                pending[intent.name] = (server_id, name, intent)
        except Exception:
            with self._metadata_lock:
                self._servers[server_id].state = ServerState.FAILED
            return failure("invalid_tool_metadata", "The MCP server supplied invalid tool metadata.")
        with self._metadata_lock:
            if self._closed or runtime.retire_requests or generation != runtime.generation:
                return failure("cancelled", "The MCP discovery was retired.")
            self._tools = {
                name: tool for name, tool in self._tools.items() if tool[0] != server_id
            } | pending
            self._servers[server_id].state = ServerState.AVAILABLE
        return ToolResult(success=True, data=tuple(deepcopy(tool[2]) for tool in pending.values()))

    def executor(self, intent_name: str, *, cancel_event: Event | None = None) -> MCPToolExecutor:
        """Resolve a discovered capability without starting a process."""
        with self._metadata_lock:
            tool = self._tools.get(intent_name.strip().lower())
        if tool is None:
            raise ValueError("MCP tool has not been discovered.")
        return MCPToolExecutor(self, *tool, cancel_event=cancel_event)

    def _permission(self, server_id, name, request, *, confirmed=False):
        definition = self._servers[server_id].definition
        if not getattr(definition, "enabled", True) or not definition.trusted or name not in definition.allowed_tools:
            return failure("permission_denied", "The MCP tool is not permitted.")
        if self._permission_decider is not None:
            try:
                decision = self._permission_decider(server_id, name, request)
            except Exception:
                decision = "deny"
            if decision == "confirm" and not confirmed:
                return failure("confirmation_required", "The MCP action requires confirmation.")
            if decision not in ("allow", "confirm"):
                return failure("permission_denied", "The MCP tool is not permitted.")
        if name in definition.confirm_tools and not confirmed:
            return failure("confirmation_required", "The MCP action requires confirmation.")
        return None

    def retire(self, server_id: str, *, timeout_seconds: float = 10.0) -> ToolResult:
        """Cancel active work for one server and wait for owned-process cleanup."""
        runtime = self._servers.get(server_id)
        if runtime is None:
            return failure("server_not_found", "The MCP server is not configured.")
        with self._metadata_lock:
            runtime.retire_requests += 1
            runtime.generation += 1
            runtime.retire_event.set()
        try:
            if not runtime.done.wait(timeout=timeout_seconds):
                return failure("cleanup_timeout", "The MCP server cleanup has not completed.")
            close_session = getattr(self._transport, "retire", None)
            if callable(close_session):
                closed = close_session(server_id, timeout_seconds=timeout_seconds)
                if isinstance(closed, ToolResult) and not closed.success:
                    return closed
            with self._metadata_lock:
                runtime.state = ServerState.RETIRED
            return ToolResult(success=True)
        finally:
            with self._metadata_lock:
                runtime.retire_requests -= 1

    def start_eager(self) -> dict[str, ToolResult]:
        """Explicit warm-up; failures remain independent of other servers."""
        return {
            name: self.discover(name) for name, runtime in self._servers.items()
            if runtime.definition.startup_mode == "eager" and getattr(runtime.definition, "enabled", True)
            and runtime.definition.trusted and not getattr(runtime.definition, "bundle_error", None)
        }

    def close(self, *, timeout_seconds: float = 10.0) -> dict[str, ToolResult]:
        """Reject new work and retire all sessions owned by this manager."""
        with self._metadata_lock:
            self._closed = True
        return {name: self.retire(name, timeout_seconds=timeout_seconds) for name in self._servers}

    def replace_definition(self, definition: LocalServerDefinition) -> ToolResult:
        """Retire before replacing configuration; invalidate the old catalog."""
        if not isinstance(definition, (LocalServerDefinition, RemoteServerDefinition)):
            raise TypeError("MCP definitions must be CODA server definition objects.")
        runtime = self._servers.get(definition.name)
        if runtime is None:
            return failure("server_not_found", "The MCP server is not configured.")
        # Hold the retirement barrier until configuration and catalog change together.
        with self._metadata_lock:
            runtime.retire_requests += 1
        try:
            result = self.retire(definition.name)
            if not result.success:
                return result
            with self._metadata_lock:
                runtime.definition = definition
                runtime.configuration_revision += 1
                runtime.retry_after = 0
                runtime.state = ServerState.CONFIGURED
                self._tools = {name: tool for name, tool in self._tools.items() if tool[0] != definition.name}
            return ToolResult(success=True)
        finally:
            with self._metadata_lock:
                runtime.retire_requests -= 1


class MCPToolExecutor:
    """Apply permissions and external validation before invoking a local MCP."""

    def __init__(self, manager, server_id, tool_name, intent, *, cancel_event=None):
        self._manager = manager
        self._server_id = server_id
        self._tool_name = tool_name
        self._intent = deepcopy(intent)
        self._cancel_event = cancel_event
        self._configuration_revision = manager._servers[server_id].configuration_revision

    def execute(self, request: IntentRequest, *, cancel_event: Event | None = None, confirmed=False) -> ToolResult:
        cancel_event = cancel_event if cancel_event is not None else self._cancel_event
        with self._manager._metadata_lock:
            current = self._manager._tools.get(self._intent.name)
        if (
            current is None or current[2] != self._intent
            or self._configuration_revision != self._manager._servers[self._server_id].configuration_revision
        ):
            return failure("tool_not_found", "The discovered MCP tool is no longer available.")
        denied = self._manager._permission(self._server_id, self._tool_name, request, confirmed=confirmed)
        if denied is not None:
            return denied
        adapter = ExternalToolAdapter(
            self._intent,
            lambda arguments: self._manager._request(
                self._server_id, "invoke", tool_name=self._tool_name,
                arguments=arguments, cancel_event=cancel_event,
                configuration_revision=self._configuration_revision,
            ),
        )
        return adapter.execute(request)
