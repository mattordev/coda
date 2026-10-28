# MCP feedback

MCP results have separate data, visual and spoken representations. The full
`ToolResult.data` is retained on `CommandResult.tool_result`; presentation never
mutates it. Conversation history continues to use `prepare_tool_context`, with
its existing local/cloud privacy rules and size limits.

## Failures

The SDK error envelope already survives in `ToolResult.data`. Coda keeps the
normalized `ToolError.code` (tool failure, connection failure, protocol error,
timeout, cancellation, etc.) rather than guessing a transport cause from server
text. Terminal/dashboard feedback includes sanitized server details. Local
diagnostics are stored in `logs/mcp/failures.log`, rotating at 256 KB with two
backups. Logging failure does not prevent feedback. Known secrets and terminal
control sequences are removed; these local logs can still contain resource
paths and should be treated as private.

Servers can supply a structured error in a standard MCP error result:

```json
{
  "isError": true,
  "content": [{"type": "text", "text": "Additional diagnostic detail"}],
  "structuredContent": {
    "error": {"code": "permission_denied", "message": "Additional diagnostic detail"}
  }
}
```

Known codes include `permission_denied`, `not_found`, `not_a_directory`,
`configuration_error`, `invalid_arguments`, `outside_allowed_roots` and
`blocked_path`. Speech uses fixed explanations for these server-reported codes;
it does not read the supplied message or private paths aloud. Explicit typed
text exceptions (`PermissionError:`, `FileNotFoundError:`,
`NotADirectoryError:`) are also recognized. These are server claims, not an
independent diagnosis. Arbitrary/unknown exceptions get an honest spoken
fallback and detailed visual feedback. Coda never follows instructions in an
error payload or replays the action to obtain a better error.

Short plain-language errors can be quoted as a server report (for example,
"The tool reported: The printer is offline."). Messages containing paths,
tracebacks, credential terms, recognized secrets or instruction-like wording
remain visual only. This is a conservative deterministic filter, not a semantic
verification of a server's claims.

If speech cannot explain a failure, inspect terminal/dashboard output and the
local log. Missing server details remain unknown; check the server's own
configuration and diagnostics. Raw SDK exception strings, subprocess stderr and
stack traces are not automatically enabled or forwarded.
Host adapter exceptions use `mcp_internal_error`; visual diagnostics retain the
exception type without exposing arbitrary private exception strings. SDK and
connection/protocol failures also retain exception types where available.
Queued callers waiting for a cold server launch receive the configured startup
allowance as well as the normal wait budget. Waiting behind an already-connected
operation still uses only the normal timeout.
