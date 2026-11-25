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

Creation and content changes remain disabled until both the write configuration
and tool allowlist are changed explicitly. No private settings are migrated or
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

## Conversational filesystem follow-ups

After listing a folder, CODA gives counts and can present names without another
filesystem call. For example, say `show filenames`, `show recent files`, or
`find Markdown files`. Filtering uses the retained immediate-folder listing;
it does not inspect file contents or recurse. After a filename search, the same
presentation requests use only its returned matches and retain partiality.

`search that folder for Markdown files` performs a fresh recursive filename
search through the normal dispatcher. `read new-report.md`, `read the newest
report`, or `read that one` performs one normal read, only when the retained
observation identifies one file. "Report" selection matches filenames containing
that word; it does not classify document contents. Duplicate names, tied newest
timestamps, missing timestamps and ambiguous pronouns require clarification.
A partial listing cannot establish the newest file in the whole folder.

The originating absolute directory and invocation are retained by the host,
separately from untrusted tool data. This focus expires after two minutes and is
invalidated by unrelated conversation, cancellation, reset, reconfiguration or
read-policy changes. A third-party result with a similar shape cannot grant
filesystem follow-up authority. Every fresh read/search rechecks current tool
permissions and server path policy; retained names never grant access.

Partial reads use plain-language scope notices; `read the next part` uses the
server's returned byte position and performs one authorised read. Metadata and
creation replies state observed facts concisely. `show tool output` retains the
technical details and original read-range header. Asking to summarize an excerpt
still uses the existing privacy-aware interpretation policy.

## Bounded content search

`search_content(path, query)` searches literal single-line text in an explicitly
authorised directory. Enable `search_content` in the exact tool allowlist;
existing read grants and discovery do not enable it automatically.

```text
mcp.filesystem.search_content {"path":"D:/Projects","query":"release notes","pattern":"*.md"}
mcp.filesystem.search_content {"path":"D:/Projects","query":"TODO","case_sensitive":true,"max_results":20}
```

Matching is case-insensitive by default; the query is escaped as literal text,
so punctuation never becomes a regular expression or shell command. Results
contain one match per matching line, with its absolute path, filename, one-based
line and character column, and a snippet of at most 240 characters. The snippet's
starting column and truncation flag identify omitted text. Repeated occurrences
on the same line contribute one result. UTF-8 and UTF-8 with a BOM are supported;
NUL-containing, non-UTF-8 and oversized files are skipped and disclosed.

| Limit | Default | Maximum |
| --- | --- | --- |
| Examined candidate files (`max_files`) | 128 | 200 |
| Matching lines (`max_results`) | 50 | 200 |
| Bytes per file (`max_file_bytes`) | 65,536 | 1,048,576 |
| Total read bytes (`max_total_bytes`) | 1,048,576 | 8,388,608 |

Filename traversal reuses `max_depth` and `max_entries` from `search_files`,
retains at most 200 candidate paths and has its own bounded metadata output.
The complete operation has a five-second cooperative deadline; match metadata
is capped at 32 KiB of serialised ASCII. A file growing during inspection may
consume one extra probe byte beyond the remaining byte budget, then scanning
stops. The host timeout/cancellation bounds blocked filesystem operations and
retires the owned server; the cooperative deadline cannot interrupt one such
operation itself.

`files_examined`, `bytes_read`, `entries_examined`, `partial`, `truncated` and
`limits_reached` describe the observation. Traversal limits use a `traversal_`
prefix. Skipped encodings/binary/large files or any exhausted limit make a scan
incomplete; no returned text is not a claim that the entire tree has no matches.
Matching paths and snippets remain available through `show tool output`; normal
speech gives a short factual result with the incomplete-scan notice.

After a listing or filename search, `find every file containing "release notes"
in that folder` performs a new content search through current tool and path
permissions. It does not mistake retained filenames for searched contents.
Policy is rechecked before each candidate and at completion; a changed policy
discards collected matches. Links, junctions, reparse points and non-regular
files are excluded and candidates are checked again before opening. The
existing application-level filesystem race limitation still applies; this is
not an OS sandbox or a consistent snapshot of files being modified elsewhere.
No new dependency or private configuration migration is required.

## Editing and appending existing text

Enable `edit_file` and/or `append_file` individually in `allowed_tools`. Both
require independent read and write grants for the existing target, and CODA
always requires a fresh one-use confirmation even if `confirm_tools` is empty.
Write grants do not grant read access. Files must be ordinary, writable UTF-8
files of at most 1 MiB; linked/reparse ancestors, hard-link aliases and privileged
set-ID files are refused. Resulting files and replacement/append text have the
same UTF-8 byte limit. Existing creation tools still never overwrite a path.

First read the complete revision; range reads cannot supply a mutation revision:

```text
mcp.filesystem.read_file {"path":"D:/Scratch/report.txt","include_revision":true}
```

The structured result includes `sha256` of the original bytes and `byte_length`.
Use `show full tool output` to inspect them. Revision reads preserve exact
newlines and any UTF-8 BOM; default reads retain their existing behaviour.
Supply that hash with the explicit mutation:

```text
mcp.filesystem.edit_file {"path":"D:/Scratch/report.txt","old_text":"draft","new_text":"reviewed","expected_sha256":"<sha256 from the revision read>"}
mcp.filesystem.append_file {"path":"D:/Scratch/report.txt","content":"Additional notes.\r\n","expected_sha256":"<current sha256>"}
```

An edit replaces one exact, case-sensitive occurrence, refusing zero or multiple
matches. Append adds exactly the supplied text without inserting newlines.
Both refuse a changed revision. Successful mutations return the new `sha256`
and byte size for subsequent explicit operations. Confirmation retains a copy
of the requested target, revision and mutation; changing or cancelling a request
does not reuse its approval for another action.

The transaction creates an exclusive `.coda-lock` sidecar and stages bytes in
an adjacent `.coda-edit-*.tmp`. It rechecks current read/write policy, file
identity and SHA-256 before replacement. Staging receives the original file's
security before content is written. Windows uses
[ReplaceFileW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew)
with an original-file backup and without flags that ignore ACL failures. POSIX
preserves ownership, mode and extended security attributes, hard-links the
original to a recovery name, then replaces the path. Transaction siblings must
also be permitted by the configured write roots and block lists. Metadata or
security-copy failures abort before replacing the original.

The stable folder lock excludes cooperative CODA text writers even when
replacement changes a file's identity or a caller uses a filename alias. Only
one text mutation per folder can run at a time; other requests fail busy.
These checks do not provide
an OS sandbox against another same-privilege process racing path changes; the
audit's existing local filesystem threat model still applies. Replacement is
not a durable database transaction or a guarantee against power loss.

Before replacement, ordinary errors clean up staging and leave the original
intact. Interrupted work, a failed replacement or failed verification can retain
`.coda-backup-*`, staging and lock files. Windows can fail after moving the
original; recovery copies are retained rather than deleted or blindly retried.
Error output includes recovery paths and whether the target may have changed.
Cancellation/timeout can kill the server before it returns those paths; inspect
the sidecar's target/staging/backup metadata in the authorised folder.

Inspect the target and retained old/new bytes before choosing a recovery action.
Locks are never automatically stolen and mutations are never automatically
replayed. Successful changes normally remove owned recovery files; if cleanup
fails, the success reply explicitly reports retained recovery paths. Restarts
do not erase this evidence. No existing private grant is expanded automatically.

## Copying files and directory trees

Enable `copy_path` separately in `allowed_tools`. CODA requires a one-use
confirmation naming both absolute paths. Every source entry needs read
authority and every destination/staging entry needs independent write authority.
The destination must not exist, including an empty directory; its parent must
already exist. A directory cannot be copied into itself or a descendant.

```text
mcp.filesystem.copy_path {"source":"D:/Projects/notes","destination":"D:/Scratch/notes-copy"}
mcp.filesystem.copy_path {"source":"D:/Projects/archive.zip","destination":"D:/Scratch/archive-copy.zip","max_bytes":67108864}
```

Copying preserves file bytes, including binary data, and the directory hierarchy.
It creates new entries using destination/creation permissions; original
ownership, ACLs, timestamps and executable bits are not implicitly cloned.
Directories are created privately on POSIX. Source data is never removed or
returned to the model as part of copying.

| Limit | Default | Allowed maximum |
| --- | --- | --- |
| Entries including the source root (`max_entries`) | 1,024 | 4,096 |
| Total file bytes (`max_bytes`) | 33,554,432 | 134,217,728 |
| Nested directory levels (`max_depth`) | 8 | 16; zero allows immediate files |

Preflight authorises the entire bounded tree before staging. Relative path
metadata is capped at 65,536 characters, and the whole operation has a
five-second cooperative deadline. Linked/reparse ancestors or descendants,
blocked entries, special files, unsafe names and active/interrupted text-folder
locks cause refusal. Blocked descendant filenames are not exposed in errors.

Files stream in chunks of at most 64 KiB; actual consumed bytes remain within
the total byte budget plus one overflow probe byte. Current configuration,
source identity and relevant metadata are rechecked during copying and before
publication. A changed source or permission policy refuses publication. These
are per-operation checks, not an atomic snapshot of a tree being modified by
another process.

Data is staged beside the destination under an exclusive `.coda-copy-*` name,
then published without overwriting a competing destination. Windows uses
[its no-replace rename behaviour](https://docs.python.org/3.11/library/os.html#os.rename);
Linux uses [renameat2 with RENAME_NOREPLACE](https://man7.org/linux/man-pages/man2/rename.2.html).
Unsupported platforms, C libraries or filesystems fail rather than falling back
to an overwrite-capable rename. Network filesystems may have uncertain outcomes
after interrupted rename requests; inspect the destination before retrying.

Preflight refusals create no copy data. Errors after staging retain the staging
path and disclose whether publication was attempted; there is no recursive
cleanup of uncertain paths. Host cancellation/timeout reaps the owned server
but can leave partial staging before a result is returned. Inspect the
authorised destination parent for `.coda-copy-*` evidence. A later explicit
copy does not remove earlier recovery data. A blocking filesystem call remains
bounded by the host transport timeout, not the cooperative deadline alone.
The audit's existing filesystem race limitations still apply.

## Explicit destructive authority and deletion

`delete_path` requires its own tool allowlist entry and an independently enabled
`destructive` configuration. Read or write grants alone never authorise removal.
Existing configurations omit this grant and therefore remain unable to delete.
An empty `read.allowed_roots` list grants no read access and is valid for a
destructive-only or write-only setup; no dummy read root is required.

```json
"destructive": {
  "enabled": false,
  "allowed_roots": [],
  "blocked_paths": ["**/.env", "**/.ssh", "**/credentials.json"],
  "require_confirmation": true
}
```

Enable this authority only for an explicitly chosen disposable/test or managed
data root. CODA always requires a fresh confirmation naming the path and whether
contents are included, even when a confirmation setting is false. Deletion is
permanent filesystem removal; it does not use a recycle bin or promise undo.

```text
mcp.filesystem.delete_path {"path":"D:/Scratch/test-only/temporary.txt"}
mcp.filesystem.delete_path {"path":"D:/Scratch/test-only/old-notes","recursive":true}
```

Without `recursive:true`, only files and empty directories can be removed. A
recursive request preflights the complete bounded tree before removing entries.
Defaults are 1,024 entries and eight nested directory levels; maxima are 4,096
entries and sixteen levels. Relative path metadata is capped at 65,536 characters,
the cooperative deadline is five seconds, and destructive/protected root lists
are capped at 64 roots each. File contents are not read, including for large or
binary files.

Filesystem roots, configured read/write/destructive roots, workspace roots and
ancestors containing them are protected. Linked/reparse ancestors, descendants,
hard-link aliases, blocked entries and transaction recovery paths are refused.
Read-side sensitive-name blocks also apply to destructive authority. Every
entry's authority and identity are rechecked before removal; configuration
changes stop execution. New entries appearing after preflight are never added
to the approved scope: final directory removal fails rather than deleting them.

Deletion acquires stable `.coda-lock` metadata files in the target's parent and
selected directories. These internal locks belong to the destructive transaction;
they do not grant general content-write authority. They exclude concurrent CODA
text/destructive operations, including across managed server processes. Ordinary
lock/setup failures remove only owned metadata locks and leave user entries
intact. Successful deletion normally removes its owned locks. Interrupted or
partially failed deletion retains locks for inspection and never automatically
replays the request. This can also block other mutations in affected folders.

Partial failures report confirmed file/directory removal counts and uncertainty
about other effects. Recovery output gives total retained lock count and at most
twenty path examples within a 32 KiB budget, with an explicit truncation flag.
Cancellation/timeout can occur after entries were already removed and before
a result is returned; inspect the remaining tree and lock metadata before any
new request or manual lock cleanup. There are no deletion backups. Host process
cleanup and the existing filesystem race limitations still apply.

All mutation tools reserve the running integration/application and its own
permissions file, even if a broad data grant overlaps them. Generic creation,
editing, copying and deletion also reserve recognised transaction/recovery names;
read tools remain available for authorised inspection. Mutation/copy paths use
valid UTF-8, at most 32,768 characters and 128 components, alongside the existing
portable-name rules. Operator configuration must be changed outside these tools;
no existing private grant is expanded automatically.

## Move and rename

`move_path` moves a file or a complete, bounded directory tree to a new absolute
path. Changing only the final filename is a rename. The source and every
descendant need destructive authority; each destination needs separate write
authority. Read grants are not required, and file contents are not read. Add
`move_path` to the operator tool allowlist only when these capabilities are
wanted. CODA always asks for one-use confirmation naming both paths and folder
contents. Existing private grants and allowlists are not expanded automatically.

```text
mcp.filesystem.move_path {"source":"D:/Scratch/managed/old.txt","destination":"D:/Scratch/managed/new.txt"}
```

The destination parent must already exist. No destination is overwritten,
including an empty directory or a competing creator. Roots/workspaces, links,
reparse entries, hard-link aliases, blocked descendants, protected operator
paths and recovery artifacts are refused. Directory moves into themselves are
refused. Case-only renames on a case-insensitive filesystem are refused as an
existing destination; use a separately confirmed intermediate filename if needed.
Cross-filesystem moves fail clearly without a copy/delete fallback. The native
Windows/Linux no-replace rename support described for copying is required.

Defaults are 1,024 entries and eight directory levels, capped at 4,096 and
sixteen. Relative path metadata is capped at 65,536 characters. Current source
and destination permissions, identities and directory membership are rechecked
before rename; new descendants are refused. The cooperative deadline is five
seconds, with blocking calls additionally subject to the host timeout. Stable
source/destination folder locks coordinate text and destructive transactions.
Successful moves preserve the original filesystem entry and normally remove
locks, including locks relocated inside moved folders. Filesystem security and
metadata follow native same-filesystem rename semantics; destination-parent
inheritance is not promised and permissions are not rewritten.

A rename may finish before cancellation, timeout or a lost network response.
Such outcomes are never automatically replayed or reported as confirmed success.
Inspect both source and destination and retained `.coda-lock` metadata before a
new request or manual cleanup. Uncertain tool results report the attempted
paths, cause and at most twenty recovery-lock examples within 32 KiB, plus a
possible relocated root when an exception leaves its location uncertain. A
success with leftover locks states that cleanup still needs operator inspection.
The existing non-sandbox filesystem race limitations apply.
