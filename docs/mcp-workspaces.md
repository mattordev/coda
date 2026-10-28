# Filesystem workspace references

The bundled filesystem integration can resolve `README` or `docs/guide.md`
within explicitly configured workspaces. This host feature supplies a concrete
absolute argument to the standalone server. It grants no additional permission.

## Configure context and permissions

Add optional `read.workspace_roots` to the same private filesystem permissions
file already selected by the bundle. The default is
`integrations/coda-filesystem/filesystem.config.json`; a definition's
`settings.config_file` selects an alternative absolute file. For example:

```json
{
  "read": {
    "workspace_roots": ["D:/Projects/Coda"],
    "allowed_roots": ["D:/Projects/Coda"],
    "blocked_paths": ["**/.env", "**/.ssh", "**/credentials.json"]
  }
}
```

Use your own absolute paths (for example `/home/matt/projects/coda` on Linux),
and preserve any other existing settings. Workspace roots define resolution
context; allowed roots and blocked patterns define authorization. Every workspace
must be an ordinary existing directory within an allowed root and outside blocked
paths. A workspace cannot grant access to an otherwise unauthorized directory.

Missing or empty workspace roots disable relative resolution, with a response
asking for configuration or an absolute path. Neither the current directory,
installation directory, user's home, nor an allowed root becomes a workspace
automatically. Existing absolute-path requests remain unchanged.

The host reads configuration for every relative invocation, so permission or
workspace edits take effect without cached resolutions. Configuration is checked
again before invocation; a change during resolution asks the user to retry.
Changes to the MCP definition itself still require restart/reconfiguration.

## Requests and matching

With a workspace containing `README.md`, both natural-language extraction of
`readme` and explicit JSON can resolve the file:

```text
coda can you read the README?
coda mcp.filesystem.read_file {"path":"README"}
coda mcp.filesystem.read_file {"path":"docs/guide.md"}
coda mcp.filesystem.list_directory {"path":"docs"}
coda mcp.filesystem.list_directory {"path":"."}
```

Natural-language requests still use the existing local classifier; resolution
adds no model calls. `read_file` must be implemented and explicitly allowlisted
in your filesystem instance. The shipped framework does not enable tools for you.
Relative resolution supports only the bundled `filesystem` identity's `read_file`
and `list_directory` tools, including renamed local instance IDs. Other bundles,
legacy explicit launch definitions and remote servers retain their own semantics.

Resolution examines only the named parent directory immediately beneath each
configured workspace. It does not recursively search projects. Parent components
must match native filesystem spelling/case rules; final entry names are compared
case-insensitively. `/` and `\` are accepted relative separators on both platforms.
For `read_file`, a basename without a dot can match the exact name or the same
basename with one of these suffixes:

```text
.md .txt .rst .json .toml .yaml .yml .py .csv
```

There is no fuzzy matching, whitespace repair, speech-to-path rewriting, arbitrary
extension guessing, environment expansion or filesystem-wide search. For example,
`git repos` is not automatically changed to `GitRepos`.

Exactly one accessible canonical match across all workspaces is required. If both
`README.md` and `README.txt` exist, or multiple workspaces contain a README,
CODA asks for clarification and makes no tool invocation. Repeat the action with
a more specific relative path or an authorized absolute path. An inaccessible
match is never offered as a candidate. No numbered chooser or pending file
selection is retained across turns.

## Bounds and safety

- At most eight configured workspaces and 256 entries in each inspected parent.
  Every entry counts, including unrelated names; exceeding the limit refuses the
  resolution even if a match was seen, because unseen entries could be ambiguous.
  Use an absolute path for larger directories.
- Relative references are limited to 1,024 characters, and the permissions file
  used for host resolution to 64 KiB. Malformed/duplicate-key configuration fails
  closed. Nothing is truncated into a guessed result.
- Traversal, empty path components, drive-relative/root-relative Windows forms,
  alternate data streams, wildcard syntax, control characters, reserved device
  names and trailing dots/spaces are rejected. `.` is accepted only to list the
  workspace root. Native absolute paths continue through existing server checks.
- Relative resolution refuses symlinks and Windows reparse points/junctions,
  including linked workspace roots, ancestors and links to targets inside an
  otherwise permitted root. Use an explicit absolute path when you intend the
  existing server's canonical-path policy to handle a link.
- Canonical allowed-root containment and blocked patterns on both lexical and
  resolved paths/ancestors are checked before inspecting a directory or accepting
  an entry. The server independently rechecks its current permissions when it
  receives the absolute path. Tool trust, schema validation and confirmation gates
  run before workspace enumeration. Confirmation executes resolution afresh.
- Cancellation aborts resolution; clarification, denial and limit failures do
  not invoke the server, retry the action or fall back to another model.

This remains an application permission check, not an OS sandbox. Another process
can race filesystem/configuration changes between checks and the server's open;
do not grant roots controlled by an adversary if that guarantee is required.
Relative resolution retains no filesystem index or candidate cache. Retained
tool-result discussion is a separate [conversation feature](mcp-conversation.md).
