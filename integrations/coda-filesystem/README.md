# Filesystem MCP

This is a standalone, read-only MCP server using the same SDK as Coda.
`list_directory` returns readable `[FILE]`/`[DIR]` text and a structured
`directory_listing` result. Expected failures include a machine-readable reason
so Coda can explain them without speaking private paths.

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
   `mcp_servers/filesystem.json`. Explicitly set `trusted` to `true` and
   `allowed_tools` to `["list_directory"]` when ready to permit that action.
3. Start Coda normally, then use manual mode, for example:
   `mcp.filesystem.list_directory {"path":"D:/Projects"}`.
   Use an absolute path contained in your configured roots.

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
are Git-ignored; commit only examples. Back up private configuration before
upgrading; broader bundle/update lifecycle work is tracked separately in #166.

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
path. There are no write tools; the reserved `write` configuration grants no
write capability.

This is an application permission check, not an OS sandbox. Resolving/checking
then opening a path has a filesystem race if another process can replace links
concurrently. Do not grant roots controlled by an adversary when that guarantee
is required. Keep permissions narrow. See [MCP feedback](../../docs/mcp-feedback.md)
for detailed visual output, speech, privacy and diagnostics behavior.
