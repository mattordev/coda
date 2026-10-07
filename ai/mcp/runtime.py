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
        for definition in definitions:
            if not isinstance(definition, LocalServerDefinition):
                raise TypeError("MCP definitions must be LocalServerDefinition objects.")
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

    def _request(self, server_id, operation, *, cancel_event=None, **kwargs):
        runtime = self._servers.get(server_id)
        if runtime is None:
            return failure("server_not_found", "The MCP server is not configured.")
        if not runtime.definition.trusted:
            return failure("permission_denied", "The MCP process is not trusted.")
        with self._metadata_lock:
            if runtime.retire_requests:
                return failure("cancelled", "The MCP server is being retired.")
            generation = runtime.generation
        deadline = time.monotonic() + runtime.definition.timeout_seconds
        while not runtime.lock.acquire(timeout=0.01):
            if cancel_event is not None and cancel_event.is_set():
                return failure("cancelled", "The MCP operation was cancelled.")
            if time.monotonic() >= deadline:
                return failure("timeout", "The MCP server is busy.")
        try:
            if cancel_event is not None and cancel_event.is_set():
                return failure("cancelled", "The MCP operation was cancelled.")
            with self._metadata_lock:
                if runtime.retire_requests or generation != runtime.generation:
                    return failure("cancelled", "The MCP operation was retired.")
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
            except Exception:
                result = failure("connection_error", "The MCP operation could not complete.")
            with self._metadata_lock:
                runtime.state = ServerState.RETIRED if result.success else ServerState.FAILED
            return result
        finally:
            with self._metadata_lock:
                if runtime.state in (ServerState.STARTING, ServerState.CONNECTED):
                    runtime.state = ServerState.FAILED
            runtime.done.set()
            runtime.lock.release()

    def discover(self, server_id: str, *, cancel_event: Event | None = None) -> ToolResult:
        """Fetch and validate an atomic capability catalog for one trusted server."""
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

    def _permission(self, server_id, name, request):
        definition = self._servers[server_id].definition
        if not definition.trusted or name not in definition.allowed_tools:
            return failure("permission_denied", "The MCP tool is not permitted.")
        if self._permission_decider is not None:
            try:
                decision = self._permission_decider(server_id, name, request)
            except Exception:
                decision = "deny"
            if decision == "confirm":
                return failure("confirmation_required", "The MCP action requires confirmation.")
            if decision != "allow":
                return failure("permission_denied", "The MCP tool is not permitted.")
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
            with self._metadata_lock:
                runtime.state = ServerState.RETIRED
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

    def execute(self, request: IntentRequest, *, cancel_event: Event | None = None) -> ToolResult:
        cancel_event = cancel_event if cancel_event is not None else self._cancel_event
        with self._manager._metadata_lock:
            current = self._manager._tools.get(self._intent.name)
        if current is None or current[2] != self._intent:
            return failure("tool_not_found", "The discovered MCP tool is no longer available.")
        denied = self._manager._permission(self._server_id, self._tool_name, request)
        if denied is not None:
            return denied
        adapter = ExternalToolAdapter(
            self._intent,
            lambda arguments: self._manager._request(
                self._server_id, "invoke", tool_name=self._tool_name,
                arguments=arguments, cancel_event=cancel_event,
            ),
        )
        return adapter.execute(request)
