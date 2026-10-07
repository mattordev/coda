# Local MCP servers

CODA's local MCP layer discovers operator-configured stdio servers and exposes
tools through the shared Intent and executor interfaces. It uses the official
Python SDK pinned to `mcp==2.3.0`. SDK types remain inside
`ai/mcp/transport.py`; callers receive CODA models and ordinary JSON values.
Automatic registration in the voice/manual runtime and natural-language
argument extraction belong to #142.

## Configuration and trust

The default directory is `mcp_servers` at the repository root. Override it with
`CODA_MCP_CONFIG_DIR` or pass a directory to `LocalMCPManager.from_directory()`.
Each server has one independent `.json` file; no index is required. A missing
directory means no servers. `.json.example` templates are ignored.

```json
{
  "id": "printer",
  "transport": "stdio",
  "command": "python",
  "args": ["/absolute/path/to/printer_server.py"],
  "trusted": true,
  "allowed_tools": ["status"],
  "env": {"PRINTER_API_KEY": "CODA_PRINTER_API_KEY"},
  "startup_timeout_seconds": 10,
  "timeout_seconds": 30,
  "max_output_bytes": 4194304,
  "max_tools": 256
}
```

`env` maps child variable names to parent environment variable names, not
literal secrets. Values use CODA's existing environment configuration and
are resolved immediately before connection. Missing references fail safely.
Only referenced values and the SDK's minimal platform environment reach the
child; the full CODA environment is not inherited. Keep secrets out of args.

Use an executable with the server's dependencies installed. CODA passes an
executable and argument list rather than constructing a shell command. `cwd`,
if supplied, must be absolute. IDs start with a lowercase letter and contain
lowercase letters, digits, underscores or hyphens. Invalid files, duplicate
IDs/JSON keys and unsupported settings produce per-file errors while valid
neighboring files continue loading. Per-file symlinks are rejected.

The directory contains operator-controlled executable configuration:

- Launch requires `trusted: true`; its default is false.
- Invocation requires the exact original tool name in `allowed_tools`, which
  defaults to empty. Trusted discovery can inspect tools without permitting
  their actions.
- Tool metadata, descriptions and annotations never grant permissions or
  select executables, arguments, credentials or working directories.
- An optional `permission_decider(server_id, tool_name, request)` can further
  restrict allowlisted actions using context. It returns `allow`, `deny` or
  `confirm`; errors/unknown values deny. `confirm` returns
  `confirmation_required` before invocation. No approval UI is implemented.
- SDK sampling, elicitation and input-required continuations are not enabled.

## Lifecycle and execution

Construction and file scanning never start processes. Each discovery or
invocation opens a fresh SDK-owned process/session on demand and closes it
afterward. The SDK handles stdin closure, bounded waits, termination/killing
and reaping, including cancellation. Operation-scoped connections incur
startup cost and do not preserve in-memory server state between calls.
Persistent sessions, eager startup and idle-retirement policy are future work.

States are `configured`, `starting`, `connected`, `available`, `retired` and
`failed`. `available` means a catalog was discovered, not that a process is
alive. Per-server locks serialize that server's work without blocking others.
Queued work has a bounded deadline and supports cancellation.
`retire(server_id)` cancels active work, invalidates queued work and waits for
cleanup; later explicit operations may reconnect. No action retries or
alternative-tool fallback occur automatically.

Startup timeout covers process launch/handshake; action timeout starts after
connection. SDK cleanup has its own bounded waits and can add several seconds.
These are synchronous APIs for CODA's execution thread; async hosts should
invoke them in a worker thread, not a running async event loop.

Tools have names such as `mcp.printer.status`. Original names are retained
privately for SDK calls and allowlists. Input schemas become Intent metadata,
and descriptive parameters are derived from their properties. Invalid or
duplicate metadata rejects the catalog atomically. Rediscovery replaces one
server's catalog; removed/changed tools invalidate existing executors.

```python
from ai.intents import IntentDispatcher, IntentRegistry, IntentRouter
from ai.mcp.runtime import LocalMCPManager

manager, configuration_errors = LocalMCPManager.from_directory()
discovered = manager.discover("printer")
if not discovered.success:
    raise RuntimeError(discovered.error.message)

registry = IntentRegistry()
dispatcher = IntentDispatcher({})
for intent in discovered.data:
    registry.register(intent)
    dispatcher.register(intent.name, manager.executor(intent.name))

matched = IntentRouter(registry).route("mcp.printer.status")
request = matched.to_request("mcp.printer.status", arguments={})
result = dispatcher.execute(request)
```

External arguments are validated before an invocation process launches.
Native parsing is unchanged. Bind cancellation for dispatcher callers with
`manager.executor(intent.name, cancel_event=request_cancel_event)`. Direct
callers can use `execute(request, cancel_event=event)`.

`ToolResult.data` retains the full JSON MCP result, including content blocks
and `structuredContent` when provided. `isError` becomes `mcp_tool_error` with
a safe message and a separate full payload. Connection, protocol, timeout,
cancellation and output-limit failures have normalized safe errors.
`max_output_bytes` caps the decoded serialized response after SDK parsing;
it is not a transport-frame or peak-memory limit. `max_tools` caps discovery;
repeated pagination cursors fail rather than loop forever.

## Privacy and context

Raw server stderr is discarded; SDK diagnostics with raw exception details
are suppressed for the operation's thread. Full payloads are not logged or
automatically inserted into conversations.

`sanitize_structure()` in the existing privacy sanitizer handles nested JSON
strings and keys. `prepare_tool_context()` applies the existing cloud policy
to the complete envelope before producing a bounded representation, which
can be blocked, sanitized or summarized. Local use retains the existing
local/cloud distinction. Full results remain unchanged. Detection retains
the existing policy's categories and limitations.

```python
from ai.tools.context import prepare_tool_context

context_text = prepare_tool_context(result, for_cloud=True, max_chars=2000)
# None means this result must not enter the cloud conversation.
```

## Tests and references

```text
python -m unittest tests.test_mcp_config tests.test_mcp_runtime tests.test_tool_context -v
```

Tests include a real disposable SDK stdio server, PID cleanup, failure
isolation, permissions, env references and nested privacy handling.

- [SDK v2.3.0 transports](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/client/transports.md)
- [MCP tool results](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
