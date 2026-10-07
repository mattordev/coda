"""Independent local MCP definitions and explicit process/tool trust."""

from dataclasses import dataclass
import json
from math import isfinite
import os
from pathlib import Path
import re

from ai.tools.models import ToolError


@dataclass(frozen=True)
class LocalServerDefinition:
    """A local process definition supplied by the operator, never by a tool."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env_refs: tuple[tuple[str, str], ...] = ()
    cwd: str | None = None
    trusted: bool = False
    allowed_tools: frozenset[str] = frozenset()
    startup_timeout_seconds: float = 10.0
    timeout_seconds: float = 30.0
    max_output_bytes: int = 4 * 1024 * 1024
    max_tools: int = 256

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.name):
            raise ValueError("MCP server id must be a lowercase identifier.")
        if not isinstance(self.command, str) or not self.command.strip() or "\0" in self.command:
            raise ValueError("MCP command must be a non-empty executable name.")
        if not isinstance(self.args, tuple) or any(
            not isinstance(arg, str) or "\0" in arg for arg in self.args
        ):
            raise ValueError("MCP args must contain strings.")
        if not isinstance(self.trusted, bool):
            raise ValueError("MCP process trust must be an explicit boolean.")
        if not isinstance(self.allowed_tools, frozenset) or any(
            not isinstance(name, str) or not name.strip() for name in self.allowed_tools
        ):
            raise ValueError("MCP allowed tools must contain non-empty names.")
        if self.cwd is not None and (
            not isinstance(self.cwd, str) or not Path(self.cwd).is_absolute()
        ):
            raise ValueError("MCP working directory must be absolute.")
        if not isinstance(self.env_refs, tuple):
            raise ValueError("MCP environment references must be pairs.")
        env_names = set()
        for pair in self.env_refs:
            if not isinstance(pair, tuple) or len(pair) != 2 or any(
                not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                for name in pair
            ):
                raise ValueError("MCP environment values must reference variable names.")
            if pair[0] in env_names:
                raise ValueError("MCP environment names cannot be duplicated.")
            env_names.add(pair[0])
        for value in (self.startup_timeout_seconds, self.timeout_seconds):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value) or value <= 0:
                raise ValueError("MCP timeouts must be positive finite numbers.")
        for value in (self.max_output_bytes, self.max_tools):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("MCP limits must be positive integers.")

    def environment(self) -> dict[str, str]:
        """Resolve only explicitly referenced values at connection time."""
        values = {}
        for child_name, parent_name in self.env_refs:
            if parent_name not in os.environ:
                raise ValueError("A configured MCP environment variable is missing.")
            values[child_name] = os.environ[parent_name]
        return values


@dataclass(frozen=True)
class ConfigurationDiscovery:
    servers: tuple[LocalServerDefinition, ...]
    errors: dict[str, ToolError]


def _read_definition(path: Path) -> LocalServerDefinition:
    def unique_keys(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("MCP JSON keys cannot be duplicated.")
            result[key] = item
        return result

    value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ValueError("MCP definition must be an object.")
    allowed_keys = {
        "id", "transport", "command", "args", "env", "cwd", "trusted",
        "allowed_tools", "startup_timeout_seconds", "timeout_seconds",
        "max_output_bytes", "max_tools",
    }
    if set(value) - allowed_keys or value.get("transport", "stdio") != "stdio":
        raise ValueError("MCP definition has unsupported settings.")
    args = value.get("args", [])
    env = value.get("env", {})
    tools = value.get("allowed_tools", [])
    if not isinstance(args, list) or not isinstance(env, dict) or not isinstance(tools, list):
        raise ValueError("MCP args, environment, or allowlist has an invalid shape.")
    if any(not isinstance(tool, str) for tool in tools) or len(tools) != len(set(tools)):
        raise ValueError("MCP allowed tools must be unique strings.")
    return LocalServerDefinition(
        name=value["id"], command=value["command"], args=tuple(args),
        env_refs=tuple(env.items()), cwd=value.get("cwd"),
        trusted=value.get("trusted", False), allowed_tools=frozenset(tools),
        startup_timeout_seconds=value.get("startup_timeout_seconds", 10.0),
        timeout_seconds=value.get("timeout_seconds", 30.0),
        max_output_bytes=value.get("max_output_bytes", 4 * 1024 * 1024),
        max_tools=value.get("max_tools", 256),
    )


def discover_definitions(directory: str | Path | None = None) -> ConfigurationDiscovery:
    """Scan per-server JSON files without starting any processes."""
    directory = Path(directory or os.getenv(
        "CODA_MCP_CONFIG_DIR", str(Path(__file__).resolve().parents[2] / "mcp_servers")
    )).resolve()
    servers = {}
    errors = {}
    if not directory.exists():
        return ConfigurationDiscovery((), {})
    if not directory.is_dir():
        return ConfigurationDiscovery((), {"directory": ToolError(
            code="configuration_error", message="The MCP configuration directory is invalid."
        )})
    for path in sorted(directory.glob("*.json")):
        try:
            if path.is_symlink() or path.resolve().parent != directory:
                raise ValueError("MCP definitions must be local files in the configuration directory.")
            definition = _read_definition(path)
            if definition.name in servers:
                raise ValueError("MCP server id is duplicated.")
            servers[definition.name] = definition
        except (ValueError, TypeError, KeyError, OSError):
            errors[path.name] = ToolError(
                code="configuration_error", message="The MCP server definition is invalid."
            )
    return ConfigurationDiscovery(tuple(servers.values()), errors)
