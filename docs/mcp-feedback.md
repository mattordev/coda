# MCP feedback

MCP results have separate data, visual and spoken representations. The full
`ToolResult.data` is retained on `CommandResult.tool_result`; presentation never
mutates it. Conversation history continues to use `prepare_tool_context`, with
its existing local/cloud privacy rules and size limits.

The execution event also carries the full `tool_result` (excluded from its
repr). The last eight complete results are retained as local snapshots until
conversation reset; host code can use `utils.llm_service.get_recent_tool_result()`
for subsequent processing. Returned snapshots cannot mutate stored results.
These are not automatically sent to models: apply `prepare_tool_context` before
any model exposure. Model-visible excerpts are explicitly labeled untrusted
data, with a matching system instruction. Retention is in memory only and
bounded by result count and the existing per-server output limit.

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

## Successful results

Terminal and dashboard receive detailed sanitized output (up to 12,000
characters); speech has its own 1,200-character budget. Truncation is marked
with an ellipsis and never changes the retained raw result.

For interoperable directory summaries a server can return
`structuredContent: {"kind": "directory_listing", "entries": [{"name":
"tests", "type": "directory"}, {"name": "main.py", "type": "file"}]}`.
An empty `entries` array means no accessible entries were returned, not proof
that the underlying directory has no blocked children. Set `truncated: true`
when entries are partial; Coda will avoid claiming complete directory totals.
Legacy text listings with one `[FILE] name` or `[DIR] name` per line also work.
Counts and examples come only from the returned data.

When every entry supplies a finite numeric `modified_at` Unix timestamp, spoken
examples use the three most recently modified entries, newest first. Missing or
invalid timestamps preserve the supplied order. The raw data and displayed
listing are not reordered. Folder timestamps describe immediate directory-entry
changes, not recursive edits inside a project.

Requests for counts, files/folders, all names or a named entry influence speech; large exact-name
requests still point to visual output when they exceed the speech budget.

Generic large arrays get item counts, and long unrecognized text is signposted
on screen rather than read in full. Short natural text passes through. Missing
content keeps the neutral completion fallback, separate from a known empty
listing. Arbitrary entry values are quoted data, never instructions or actions.

Summarization is deterministic and makes no LLM calls, so it adds no model cost,
network latency or new cloud exposure. Optional LLM summarization was considered
and deferred: predictable structures cover the current need. Any future opt-in
summarizer must have bounded latency/cost, preserve original-question grounding,
apply local/cloud privacy policy and fall back safely without replaying tools.
