# Remote MCP servers

CODA supports Streamable HTTP through the official pinned `mcp==2.3.0` SDK.
Legacy HTTP+SSE is not a supported configuration transport. Remote tools use the
same Intent, schema validation, permissions, normalized results and text/voice
routing as [local servers](mcp-local.md).

Place one JSON file in `mcp_servers` (or `CODA_MCP_CONFIG_DIR`):

```json
{
  "id": "remote",
  "transport": "streamable-http",
  "url": "https://your-server.example/mcp",
  "header_env": {"Authorization": "CODA_REMOTE_AUTHORIZATION"},
  "trusted": false,
  "allowed_tools": [],
  "confirm_tools": [],
  "session_mode": "persistent",
  "startup_mode": "lazy",
  "idle_timeout_seconds": 300,
  "startup_timeout_seconds": 10,
  "timeout_seconds": 30,
  "reconnect_backoff_seconds": 2,
  "max_output_bytes": 4194304,
  "max_sse_event_bytes": 4194304,
  "max_tools": 256
}
```

The environment variable contains the entire header value, for example
`Bearer <token>`. Secrets remain outside definition files, metadata and error
messages. Values are resolved when connecting; missing/empty references and
newline injection fail safely. Other API-key headers can also reference variables.
Case-insensitive duplicate headers and overrides of protocol/session headers are
rejected. URLs cannot contain userinfo, queries or fragments. HTTPS is required
except `localhost`, `127.0.0.1` and `::1` loopback HTTP for local development.
TLS certificate verification stays enabled. Ambient proxy/HTTP credentials are
not inherited (`trust_env=False`). Interactive OAuth login, token refresh, custom
TLS/proxy configuration and credential-management UI are not implemented.

Construction and configuration discovery never connect. `trusted: true` permits
connection/discovery; exact tool names must also be in `allowed_tools` to invoke.
`confirm_tools` requires the runtime's explicit one-use confirmation. Denied,
confirmation-required and failed actions do not trigger unrelated fallback.

`operation` (default) opens/closes a client per discovery/call. `persistent`
retains a client and its negotiated protocol state with serialized per-server
access. `lazy` is the startup default; explicitly configured `eager` requires
persistent mode. Idle policy is identical to local support: `null` retains until
retirement/shutdown, a positive number retires after completed work has been idle
that long. CODA closes its HTTP connections/sessions, including the SDK's session
termination request where applicable. It does **not** stop the remote server.
Remote in-memory state persistence depends on the server's own implementation;
retaining a CODA client is not a guarantee about remote deployment state.

Startup and action deadlines, cancellation, malformed responses and disconnects
use the same safe outcome envelope. HTTP 401/403 becomes `authentication_error`;
HTTP 413 becomes `output_limit`. An SSE event exceeding its transport limit becomes
`mcp_protocol_error`. Other protocol/connection failures remain normalized without
raw SDK diagnostics. `max_sse_event_bytes` accepts a positive integer or `null`
to disable that event cap. JSON response bodies are unaffected by the SSE limit.
`max_output_bytes` checks the decoded serialized full result **after parsing**;
neither it nor the SSE cap is a universal JSON transport/peak-memory ceiling.
The remote server independently controls its request/body limits.

Timeout/cancellation conservatively retires a persistent connection. Later calls
may reconnect; failed actions are never replayed by CODA. Optional
`reconnect_backoff_seconds` rejects premature later requests as
`connection_backoff`; it does not schedule retries. The SDK may resume an existing
SSE response via GET, using its bounded protocol resumption behavior; CODA never
resubmits a `tools/call` to recover. Full results stay separate from bounded
privacy-aware conversation history.

Tests use `tests/mcp_http_server.py`, an authenticated disposable loopback server,
and `tests/test_mcp_remote.py`. The fixture belongs to framework validation and is
not an integration for a real service.

Troubleshooting:

- `configuration_error`: check supported keys, endpoint and environment references.
- `permission_denied`: check process/endpoint trust and the exact original allowlist.
- `authentication_error`: check the complete header value and server authorization.
- `connection_error`: check reachability, endpoint path and certificate trust.
- `timeout`: adjust startup/action timeouts for legitimate work; avoid automatic replay.
- `output_limit`/`mcp_protocol_error`: inspect server payload settings and event limits.
- Failed first discovery: reconfigure/restart to retry; per-turn discovery is not retried.

[Pinned SDK transport documentation](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/client/transports.md)
describes Streamable HTTP, same-origin redirects and client ownership.
