# Filesystem MCP

This is a standalone MCP server using the same SDK as Coda. Reads and creation
have separate permissions; all creation access is disabled by default.
`list_directory` returns readable `[FILE]`/`[DIR]` text and a structured
`directory_listing` result. Expected failures include a machine-readable reason
so Coda can explain them without speaking private paths.

`read_file` reads an explicitly permitted UTF-8 file, up to 1 MiB of bytes.
It returns the original text (with universal newline handling), including exact
versions and identifiers, with structured kind `file_contents`. Oversized,
non-UTF-8, missing and directory inputs return errors. The actual read is bounded
even if a file grows after its size was checked. File contents remain untrusted
data and pass through Coda's existing tool-result privacy policy.

### Bounded ranges in large files

Use `max_bytes` to read a bounded portion of a large UTF-8 file without loading
the preceding content. `offset_bytes` defaults to zero. For example:

```text
mcp.filesystem.read_file {"path":"D:/Projects/report.txt","max_bytes":4096}
mcp.filesystem.read_file {"path":"D:/Projects/report.txt","offset_bytes":4096,"max_bytes":4096}
```

For continuation, use the returned `next_offset_bytes` rather than adding the
requested budget: the server can shorten a chunk to keep a UTF-8 character or
CRLF pair together. Offsets count original file bytes; output retains the
existing newline normalisation. A byte offset inside a UTF-8 character is
rejected. Very small budgets that cannot fit the next character/newline are
rejected rather than returning a continuation that cannot advance.

`max_bytes` must be an integer from 1 to 1,048,576. A nonzero offset without an
explicit budget uses 65,536 bytes. Each range reads at most its byte budget plus
one lookahead byte and seeks directly to the offset. The legacy call without
range arguments still reads a complete file only when it fits the 1 MiB cap.

Range results include `partial`, `offset_bytes`, `bytes_read`, `has_more` and
`next_offset_bytes`. A missing suffix or prefix is labelled partial in both the
text and structured data, including when a tail range reaches EOF. An offset at
EOF returns an empty range; an offset beyond EOF is rejected. Invalid UTF-8 in
the selected range is rejected; unread portions are not validated. Repeated
ranges observe the file at each call, so concurrent edits can change its bytes;
this is not a snapshot or an automatic multi-call read loop.

Current roots, blocked paths, tool grants and cancellation apply to each call.
No configuration migration or new dependency is required. A cancelled MCP
operation is retired through the existing transport; an individual bounded
local file read has no separate mid-read cancellation mechanism.

Each entry also supplies its `modified_at` timestamp. The displayed listing stays
alphabetical. General requests get spoken counts, recent-edits requests get the
three newest files, and explicit filename requests get names within the speech limit.
Folder timestamps reflect changes to immediate entries, not edits throughout a project.

## Enable in Coda

1. Copy `filesystem.config.json.example` to `filesystem.config.json` in this
   directory. Add only the absolute directories you want Coda to read to
   `read.allowed_roots`. An empty list grants no access. Configure
   `read.blocked_paths` as needed; the example blocks common sensitive names.
2. Copy `mcp_servers/filesystem.json.example` to
   `mcp_servers/filesystem.json`. Explicitly set `enabled` and `trusted` to `true` and
   `allowed_tools` to `["list_directory"]` when ready to permit that action.
3. Start Coda normally, then use manual mode, for example:
   `mcp.filesystem.list_directory {"path":"D:/Projects"}`.
   Use an absolute path contained in your configured roots.

To permit file reads as well, explicitly add `"read_file"` to `allowed_tools`.
An upgrade exposing the tool does not grant it permission. For example:
`mcp.filesystem.read_file {"path":"D:/Projects/README.md"}`. Keep blocked paths
configured: file reads enforce the same root and blocked-ancestor rules as listings.

Optional `read.workspace_roots` supplies explicit context for relative references
such as `README` or `docs/guide.md` in Coda's bundled read tools. It grants no read
permission and defaults to an empty list. See [workspace references](../../docs/mcp-workspaces.md)
for configuration, bounded matching, clarification and link restrictions.

The bundled definition selects `"bundled_server": "filesystem"`. Coda uses
its active Python interpreter and resolves the fixed server entrypoint from
its installation directory. No `G:/GitRepos/coda` path or PATH-dependent
`python` command is required. The same definition works after relocating the
checkout and when Coda starts from another working directory. Bundle selection
grants neither process trust nor tool permissions. Legacy explicit command/args
definitions continue to work; bundled definitions cannot mix those launch fields.

For an existing local definition, replace `command` and `args` with
`"bundled_server": "filesystem"`. Preserve your trust, allowlist, confirmation,
environment and session choices. User definitions and filesystem permissions
are Git-ignored; commit only examples. Existing bundled definitions must add
`"enabled": true` to opt in after upgrading. No private file is migrated automatically.
Supported archive updates preserve private definitions and `*.config.json` files
beside installed servers. See [bundled MCP lifecycle](../../docs/mcp-bundled.md)
for settings, inspection and update/rollback behavior.

For other MCP clients, run this script with an interpreter containing the
project's locked MCP SDK. Optional `--config /absolute/permissions.json` selects
another permissions file. By default the file is located beside `server.py`,
independent of the client's working directory. The server imports no Coda code.

## Permission and output behavior

The requested path is canonicalized and checked for containment in an explicitly
allowed root. Blocked patterns are checked against the requested path and each
ancestor, using both the original absolute name and resolved target. Listing
entries are checked for both containment and blocked paths, so links to targets
outside the roots are omitted. `fnmatchcase` matches normalized, case-folded
strings; this is not a path-aware recursive glob. A leading `**/` also checks
the root-relative spelling to handle blocked ancestor names.

Blocked entries are omitted. An empty result means no accessible entries were
returned, which can include a directory containing only blocked entries.
Configuration, missing paths, non-directory paths and permission denials have
distinct structured codes. Unexpected exceptions still use the SDK failure
path. Creation tools require a separate write grant as described below.

## Create files and folders

`create_file(path, content="")` creates a new UTF-8 file, optionally with up to
1 MiB of UTF-8 bytes. Omitting content creates an empty file.
`create_directory(path)` creates one new directory. Both require an absolute
path with an existing parent and fail when the destination already exists.
Files are opened in exclusive creation mode, so an existing file is never
truncated or overwritten, including when another process creates it first.
These tools do not edit, delete, rename or recursively create parent directories.

To opt in, add a `write` object to your private filesystem configuration:

```json
"write": {
  "enabled": true,
  "allowed_roots": ["D:/Projects/Scratch"],
  "blocked_paths": ["**/credentials.json"],
  "require_confirmation": true
}
```

Use your own existing directory. Empty or omitted write roots grant no access;
read roots and workspace roots never grant creation access. Read-side blocked
paths also apply to creation, alongside any extra write-side blocks. Symlinks,
Windows junctions, traversal and ambiguous Windows names are rejected.

Explicitly add `create_file` and/or `create_directory` to the MCP definition's
`allowed_tools`; also list them in `confirm_tools` to document your intent.
Coda always requires its existing one-use confirmation for these bundled create
tools, even if `confirm_tools` is empty or the legacy `write.require_confirmation`
field is false. Creation is not enabled automatically by discovering new tools.
With another MCP client, configure confirmation in that client: the standalone
server enforces filesystem permissions but does not present a confirmation UI.

For example, after setup:

```text
mcp.filesystem.create_directory {"path":"D:/Projects/Scratch/notes"}
mcp.filesystem.create_file {"path":"D:/Projects/Scratch/notes/todo.txt","content":"Review the draft.\n"}
```

Existing installations remain read-only until both the write configuration and
tool allowlist are changed explicitly. No private settings are migrated or
expanded. These additions are separate from v1.5.5.
An I/O failure after exclusive file creation can leave a partial new file; the
tool reports failure, and does not delete it or replay the operation.

This is an application permission check, not an OS sandbox. Resolving/checking
then opening a path has a filesystem race if another process can replace links
concurrently. Do not grant roots controlled by an adversary when that guarantee
is required. Keep permissions narrow. See [MCP feedback](../../docs/mcp-feedback.md)
for detailed visual output, speech, privacy and diagnostics behavior.

## File metadata

`get_file_metadata(path)` inspects an absolute, read-authorised file or directory
without reading file contents or recursively scanning a directory. Add
`get_file_metadata` to the definition's exact `allowed_tools` list to enable it;
existing read grants do not automatically allow a newly discovered tool.

```text
mcp.filesystem.get_file_metadata {"path":"D:/Projects/report.txt"}
```

Results include the canonical path, entry type, file `size_bytes` and observed
`modified_at` in Unix seconds. Text renders the modification time in UTC. File
sizes count bytes, including for binary files and files larger than the read
limit. Directory/other entry sizes are `null`: they are not recursively computed
tree sizes. Creation times are omitted because platform-specific `ctime` does
not consistently mean creation time.

The same current read roots and lexical/resolved blocked-ancestor rules apply.
Outside or blocked symlink/junction targets reveal no metadata. Relative
workspace resolution currently applies to `read_file` and `list_directory`;
provide an absolute path for metadata. Metadata is a per-call observation and
can change immediately afterwards. No new dependency or migration is required.

## Bounded filename search

`search_files(path, pattern)` searches file basenames beneath an explicit,
read-authorised absolute directory. Add `search_files` to the MCP definition's
exact `allowed_tools` list to enable it. Discovery grants no new permission.

```text
mcp.filesystem.search_files {"path":"D:/Projects","pattern":"*.md"}
mcp.filesystem.search_files {"path":"D:/Projects","pattern":"README.md","max_depth":2,"max_results":20}
```

Matching is case-insensitive on Windows and Linux. Patterns use filename glob
syntax (`*`, `?`, character classes), without directory separators. `*.md`
matches Markdown filenames at every examined depth. Only regular files match;
file contents are never read. Descendant symlinks, junctions and reparse points
are skipped, including links to another permitted root and directory cycles.
An explicitly supplied root is authorised using the existing absolute-path
policy before traversal; no home, current directory or workspace is inferred.

| Limit | Default | Allowed range |
| --- | --- | --- |
| `max_depth` | 5 | 0–16; zero examines immediate files |
| `max_entries` | 4,096 | 1–10,000 enumerated names |
| `max_results` | 50 | 1–200 matches |

Traversal also has a five-second cooperative deadline. Match metadata is capped
at 32,768 serialised ASCII characters, independently of the MCP transport limit.
Directories are processed breadth first, with names sorted within each fully
enumerated directory. An oversized directory uses at most one extra enumeration
step to detect the entry limit; its arbitrary enumeration prefix is discarded.
Earlier complete directories can still contribute matches. Narrow the requested
directory or adjust the bounded limits when a search is incomplete.

`matches` retains exact authorised paths, names and observed modification times.
`partial`, `truncated` and `limits_reached` disclose depth, entry, result, output,
time or unavailable-entry limits. Reaching the result cap is conservatively
reported as partial even if it coincides with the final match. Normal replies
retain that scope notice and show a few names; `show tool output` exposes all
returned paths. A filename search is not presented as a complete directory
listing or a content search.

Read policy is reloaded before each directory, periodically during enumeration
and processing, and before returning results. A policy change discards collected
observations and denies the request. Existing MCP cancellation retires the owned
operation/server; directory iterators are closed when limits stop traversal.
The cooperative deadline cannot interrupt an individual blocked filesystem call;
the host transport timeout/cancellation remains the outer bound. Existing
application-level filesystem race limitations still apply. No new dependency or
private configuration migration is required.
