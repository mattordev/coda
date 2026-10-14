# Local MCP servers

CODA's local MCP layer discovers operator-configured stdio servers and exposes
tools through the shared Intent and executor interfaces. It uses the official
Python SDK pinned to `mcp==2.3.0`. SDK types remain inside
`ai/mcp/transport.py`; callers receive CODA models and ordinary JSON values.
The manual/voice runtime loads definitions during intent configuration, starts
explicitly eager servers in a background warm-up, and discovers remaining trusted
servers on the first unmatched lookup. Native matches do not wait for lazy MCP
discovery. Explicit `mcp.<server>.<tool>` requests discover only their named server
before deterministic matching, avoiding a first-use local-classifier wait.
Unknown explicit tool names return unavailable without model interpretation.
Optional servers discover concurrently and failures remain isolated.

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

`confirm_tools` is an optional subset of `allowed_tools`. It requires a one-use
confirmation for those actions even on a trusted server. CODA asks the user to
say/type `confirm` or `cancel`; approval expires after 60 seconds, is cleared by
other input/cancellation/reconfiguration, and never grants permission to a denied
tool. Dynamic permission callbacks can still deny a previously confirmed action.

## Lifecycle and execution

Construction and file scanning never start processes. Each discovery or
invocation opens a fresh SDK-owned process/session on demand and closes it
afterward. The SDK handles stdin closure, bounded waits, termination/killing
and reaping, including cancellation. Operation-scoped connections incur
startup cost and do not preserve in-memory server state between calls.
Set `session_mode` to `persistent` to reuse one process/session across discovery
and calls. The default `operation` mode retains the behavior above. Persistent
sessions preserve server in-memory state until retirement or failure. They cost
resources while idle but avoid repeated startup latency.

`startup_mode` defaults to `lazy`. Persistent servers may opt into `eager`;
the host explicitly calls `manager.start_eager()` after construction. Each
server returns its own outcome; an unavailable optional server does not disable
other servers. Configuration scanning and manager construction still launch
nothing. `idle_timeout_seconds` accepts a positive finite duration or `null`
(the default), which retains a persistent session until explicit cleanup.
Idle time begins after a completed operation, never during an active call.

Each persistent SDK session has one owning worker thread/async task. Per-server
serialization makes concurrent first use create one session. Timeout or active
cancellation retires the session conservatively; a later explicit request can
connect again, but the failed action is never automatically replayed. Domain
tool errors and output-limit results do not by themselves close a healthy
session. Server state is lost whenever a process/session is replaced.

`manager.replace_definition(definition)` retires the old process before changing
configuration and removes its catalog. Discover again to obtain fresh executors.
`manager.close()` rejects further requests and retires every owned session.
Hosts must call it during shutdown. Explicit retirement permits later requests;
shutdown does not. SDK cleanup retains its own bounded teardown waits.

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

## Text and voice requests

The shared local classifier receives tool names, descriptions and input schemas.
It can select a capability and return structured arguments. Those arguments are
validated before invocation. Exact manual requests also work without a model:
`mcp.printer.status {"detail": true}`. If selection has no argument object, the
runtime uses a configured local provider to extract explicit values against the
selected schema. Missing/invalid values ask for a fresh clarified request; CODA
does not invent defaults or coerce types. Native commands retain their parsing.

Successful tool results are presented as readable bounded text, with detected
secrets sanitized. MCP text blocks are preferred, JSON text becomes readable
values, and structured content supplies a fallback. The result envelope and
protocol metadata are not spoken; echo output is just its returned message.
`CommandResult.tool_result` retains the separate full envelope.
Conversation history receives at most 2000 characters each for local and cloud
representations; cloud policy is applied to the full result before truncation.
Follow-up questions can use that history. Accepted failures, permission outcomes
and ambiguous candidates never fall through to unrelated conversation actions.
Genuinely unmatched requests keep the existing general LLM fallback.

Failed discovery is not retried on every conversation turn; reconfigure/restart
to retry a failed initial catalog. A disconnected discovered server can reconnect
on a later action without replaying the failed action. Confirmation is held in
memory for the single-user runtime and does not survive shutdown.

## Tests and references

```text
python -m unittest tests.test_mcp_config tests.test_mcp_runtime tests.test_tool_context -v
```

Tests include a real disposable SDK stdio server, PID cleanup, failure
isolation, permissions, env references and nested privacy handling.

- [SDK v2.3.0 transports](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/client/transports.md)
- [MCP tool results](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
