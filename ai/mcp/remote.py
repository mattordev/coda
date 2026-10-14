"""Operator-controlled remote endpoints and environment-backed HTTP headers."""

from dataclasses import dataclass
from math import isfinite
import os
import re
from urllib.parse import urlsplit

from ai.mcp.config import LocalServerDefinition


@dataclass(frozen=True)
class RemoteServerDefinition:
    name: str
    url: str
    header_env_refs: tuple[tuple[str, str], ...] = ()
    trusted: bool = False
    allowed_tools: frozenset[str] = frozenset()
    confirm_tools: frozenset[str] = frozenset()
    startup_timeout_seconds: float = 10.0
    timeout_seconds: float = 30.0
    max_output_bytes: int = 4 * 1024 * 1024
    max_tools: int = 256
    session_mode: str = "operation"
    startup_mode: str = "lazy"
    idle_timeout_seconds: float | None = None
    max_sse_event_bytes: int | None = 4 * 1024 * 1024
    reconnect_backoff_seconds: float = 0.0

    def __post_init__(self):
        # Reuse existing trust/lifecycle validation without exposing SDK models.
        LocalServerDefinition(
            self.name, "remote", trusted=self.trusted, allowed_tools=self.allowed_tools,
            confirm_tools=self.confirm_tools, startup_timeout_seconds=self.startup_timeout_seconds,
            timeout_seconds=self.timeout_seconds, max_output_bytes=self.max_output_bytes,
            max_tools=self.max_tools, session_mode=self.session_mode,
            startup_mode=self.startup_mode, idle_timeout_seconds=self.idle_timeout_seconds,
        )
        if not isinstance(self.url, str) or any(character.isspace() for character in self.url):
            raise ValueError("MCP endpoint must be an HTTP URL.")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in ("https", "http") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
        ):
            raise ValueError("MCP endpoint requires HTTPS except on loopback; use header references for credentials.")
        if parsed.port is not None and not 0 < parsed.port < 65536:
            raise ValueError("MCP endpoint port is invalid.")
        if not isinstance(self.header_env_refs, tuple):
            raise ValueError("MCP HTTP headers must reference environment variables.")
        seen = set()
        for pair in self.header_env_refs:
            if (
                not isinstance(pair, tuple) or len(pair) != 2
                or not isinstance(pair[0], str) or not re.fullmatch(r"[A-Za-z0-9-]+", pair[0])
                or not isinstance(pair[1], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pair[1])
            ):
                raise ValueError("MCP HTTP headers must reference environment variables.")
            name = pair[0].lower()
            if name in seen or name in ("host", "content-length", "content-type", "accept", "mcp-session-id", "mcp-protocol-version"):
                raise ValueError("MCP protocol headers cannot be overridden or duplicated.")
            seen.add(name)
        limit = self.max_sse_event_bytes
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
            raise ValueError("MCP SSE event limit must be a positive integer or null.")
        delay = self.reconnect_backoff_seconds
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not isfinite(delay) or delay < 0:
            raise ValueError("MCP reconnect backoff must be a non-negative finite number.")

    def headers(self):
        values = {}
        for name, variable in self.header_env_refs:
            value = os.environ.get(variable)
            if value is None or not value.strip() or "\r" in value or "\n" in value:
                raise ValueError("An MCP HTTP credential reference is unavailable.")
            values[name] = value
        return values
