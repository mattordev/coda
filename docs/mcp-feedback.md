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
text. The conversational console/dashboard reply matches speech; sanitized server
details are available through `show tool output` or debug output. Local
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

If speech cannot explain a failure, request `show tool output` and inspect the
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

Console `CODA:`, dashboard response and queued speech share one finalized reply
(up to 1,200 characters). Detailed sanitized output (up to 12,000 characters)
remains on the execution result and is printed under `[MCP OUTPUT]` in debug mode
or when requested with `show tool output`. `show full tool output` exposes the
redacted normalized JSON envelope without readable-renderer depth/item limits,
subject to an explicit 8 MiB console cap. Truncation never changes retained data.

For interoperable directory summaries a server can return
`structuredContent: {"kind": "directory_listing", "entries": [{"name":
"tests", "type": "directory"}, {"name": "main.py", "type": "file"}]}`.
An empty `entries` array means no accessible entries were returned, not proof
that the underlying directory has no blocked children. Set `truncated: true`
when entries are partial; Coda will avoid claiming complete directory totals.
Legacy text listings with one `[FILE] name` or `[DIR] name` per line also work.
Counts and examples come only from the returned data.

Speech selects a style from the original question:

- **Overview** (default): counts of files and directories, without example names.
- **Recent files** (for example, "What have I edited recently?"): the three most
  recently modified files, newest first. All returned files need finite numeric
  `modified_at` Unix timestamps; missing/invalid dates produce an honest fallback.
- **All names** (for example, "Read all filenames"): names in the supplied order,
  respecting file/folder filters and the speech budget.

Filename dots are rendered as the word "dot" in spoken names, including leading
dots and multiple extensions (".gitignore", "archive.tar.gz"). Names are sanitized
before punctuation changes. Displayed names and retained data preserve their
original spelling; ordinary sentence punctuation is not rewritten.
Known final file extensions also use explicit spoken forms: ".md" becomes
"dot markdown" and ".json" becomes "dot jay son", case-insensitively. Unknown
extensions retain their spelling, and folder names are not treated as file
extensions.

The raw data and displayed listing are not reordered. Explicit JSON command
arguments and path names do not select a response style. Folder timestamps
describe immediate directory-entry changes, not recursive edits inside a
project. Recent project activity is explicitly unsupported until a separate
bounded project scan is implemented.

Requests for counts, files/folders, all names or a named entry influence speech; large exact-name
requests still point to visual output when they exceed the speech budget.

Short natural text passes through. Missing
content keeps the neutral completion fallback, separate from a known empty
listing. Arbitrary entry values are quoted data, never instructions or actions.

Known directory summaries remain deterministic and make no LLM calls. Automatic
interpretation of long/complex or explicitly requested explanations uses the
existing privacy-aware provider router with bounded, stateless input and a safe
fallback. Set `CODA_MCP_INTERPRETATION=direct` for deterministic presentation only.
See [conversational interpretation](mcp-conversation.md) for policy, timeouts,
partial excerpts, follow-up, inspection commands and limitations.
