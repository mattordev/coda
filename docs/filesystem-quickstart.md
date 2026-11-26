# Filesystem setup, migration and safe test route

The bundled filesystem supports conversational folder results and bounded file
management. Every action needs an exact tool allowlist entry and independent
filesystem authority. Nothing is enabled by installing an update. The shipped
definition is disabled/untrusted with an empty allowlist; write and destructive
permissions are disabled. Use this route on a fresh disposable directory and a
separate test definition, so existing private definitions and grants stay intact.

## Tools and permissions

| Tools | Filesystem authority | Confirmation |
| --- | --- | --- |
| `list_directory`, `read_file`, `get_file_metadata`, `search_files`, `search_content` | Read roots and blocks | No mutation approval |
| `create_file`, `create_directory` | Write destination | Always |
| `edit_file`, `append_file` | Read and write target; expected SHA-256 revision | Always |
| `copy_path` | Read every source; write every destination | Always |
| `move_path` (including rename) | Destructive source; write destination | Always |
| `delete_path` | Destructive target; explicit recursive scope for nonempty folders | Always |

Read, write and destructive roots do not grant each other's authority. Read-side
sensitive-name blocks also restrict mutations. Workspace roots supply relative
read/list context and grant no access. Move/delete do not need content-read
permission. All mutations require fresh one-use approval in CODA's bundled
integration even if a confirmation setting is false. Approval expires after
60 seconds, binds the exact arguments and rechecks current permissions.

The server uses the existing MCP SDK and standard library; these features add no
filesystem dependency or separate shell search service. Supported native mutation
semantics are tested on Windows/Linux with Python 3.11/3.12. See the
[integration contract](../integrations/coda-filesystem/README.md) for byte, entry,
depth, output and time limits and [validation evidence](capability-filesystem-validation.md).

## 1. Create disposable data

In PowerShell, create a new unique directory and seed one report. This deliberately
does not reuse an existing project or previous test folder:

```powershell
$codaSmokeRoot = Join-Path ([IO.Path]::GetTempPath()) ('coda-fs-smoke-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $codaSmokeRoot -ErrorAction Stop
$codaScratch = Join-Path $codaSmokeRoot 'data'
New-Item -ItemType Directory -Path $codaScratch -ErrorAction Stop
[IO.File]::WriteAllText((Join-Path $codaScratch 'report.md'), 'Alpha report')
$codaScratch.Replace('\', '/')
(Join-Path $codaSmokeRoot 'permissions.json').Replace('\', '/')
```

On Linux, use `mktemp -d -t coda-fs-smoke-XXXXXXXX`, create a `data` subdirectory,
and create `data/report.md` containing `Alpha report`. Keep the absolute paths.
Replace **every** `<scratch>` below with that exact path, using `/` in JSON.
Replace `<permissions>` with the printed permissions-file path beside `data`.
The placeholders themselves are not valid configuration values. Keep operator
configuration outside the data directory.

## 2. Enable read-only testing

Create the new file at `<permissions>` with the following configuration, based
on `integrations/coda-filesystem/filesystem.config.json.example`. Keep existing
private files unchanged:

```json
{
  "read": {
    "allowed_roots": ["<scratch>"],
    "workspace_roots": [],
    "blocked_paths": ["**/.env", "**/.ssh", "**/credentials.json"]
  },
  "write": {"enabled": false, "allowed_roots": [], "require_confirmation": true},
  "destructive": {"enabled": false, "allowed_roots": [], "require_confirmation": true}
}
```

Copy `mcp_servers/filesystem.json.example` to a new private
`mcp_servers/filesystem-smoke.json`, and set it to explicitly enable only read
tools. If that file/ID already exists, choose an unused name and substitute its
ID in every `mcp.filesystem_smoke` command below:

```json
{
  "id": "filesystem_smoke",
  "transport": "stdio",
  "bundled_server": "filesystem",
  "enabled": true,
  "trusted": true,
  "settings": {"config_file": "<permissions>"},
  "allowed_tools": ["list_directory", "read_file", "get_file_metadata", "search_files", "search_content"],
  "session_mode": "persistent",
  "startup_mode": "lazy"
}
```

The explicit settings path selects only this test's permissions file. Restart
CODA after changing a definition so discovery/allowlists refresh. Ordinary filesystem
permission changes are rechecked by the server on each invocation. Changes to
the host-derived operator protection set require session retirement/refresh or
a restart as well.

Start from the intended checkout with `python main.py -m` using the project's
environment. The following are inputs to CODA's **manual prompt**, not PowerShell
commands. They include the default wake word `coda`; use your configured word
if different:

```text
coda mcp.filesystem_smoke.list_directory {"path":"<scratch>"}
coda show filenames
coda find all the Markdown files in that folder
coda read the newest report
coda mcp.filesystem_smoke.get_file_metadata {"path":"<scratch>/report.md"}
coda mcp.filesystem_smoke.search_files {"path":"<scratch>","pattern":"*.md"}
coda mcp.filesystem_smoke.search_content {"path":"<scratch>","query":"Alpha"}
coda mcp.filesystem_smoke.read_file {"path":"<scratch>/report.md","max_bytes":8}
coda read the next part
```

Expect one returned report, real file metadata, one matching line, and labelled
partial reads. `find all the Markdown files in that folder` filters the retained
names; `search that folder for Markdown files` makes a fresh bounded search.
An explicit request to this test instance for a path outside `<scratch>` must
be denied. A blocked `.env` file
within it must not be read or searched. Missing or ambiguous context asks which
file/folder is meant. See [conversation semantics](mcp-conversation.md).

Canonical JSON requests bypass natural-language selection. If a local model
needs longer to select a tool, consult the interpretation/intent timeout guidance
in that document; a classifier timeout runs no tool action. `coda show tool
output` exposes redacted returned details without rerunning the action.

## 3. Opt into writes for this scratch root

Only after the read checks pass, enable `write.enabled`, set its `allowed_roots`
to `["<scratch>"]`, and add these exact tools to the existing allowlist:

```json
["create_directory", "create_file", "edit_file", "append_file", "copy_path"]
```

This list is added to the read-tool list; it is not the complete definition.
Restart after the allowlist change. Leave destructive authority disabled.

```text
coda mcp.filesystem_smoke.create_directory {"path":"<scratch>/drafts"}
coda cancel
coda mcp.filesystem_smoke.create_directory {"path":"<scratch>/drafts"}
coda confirm
coda mcp.filesystem_smoke.create_file {"path":"<scratch>/drafts/note.txt","content":"Alpha"}
coda confirm
coda mcp.filesystem_smoke.read_file {"path":"<scratch>/drafts/note.txt","include_revision":true}
coda show full tool output
```

Cancellation must leave `drafts` absent. A fresh confirmation then creates it;
creating `note.txt` leaves exact bytes `Alpha`. Repeating creation must refuse
the existing destination. Before each confirmation, verify the named target.
No implicit parent creation, overwrite or overwrite prompt is offered.

Copy the `sha256` value from the complete revision read into `<sha256>`:

```text
coda mcp.filesystem_smoke.append_file {"path":"<scratch>/drafts/note.txt","content":" Beta","expected_sha256":"<sha256>"}
coda confirm
coda mcp.filesystem_smoke.read_file {"path":"<scratch>/drafts/note.txt","include_revision":true}
coda show full tool output
```

Get the **new** SHA-256 before editing; an old revision must fail with unchanged
bytes. It prevents a model or user from silently replacing someone else's
intervening change. With the new value:

```text
coda mcp.filesystem_smoke.edit_file {"path":"<scratch>/drafts/note.txt","old_text":"Alpha","new_text":"Gamma","expected_sha256":"<sha256>"}
coda confirm
coda mcp.filesystem_smoke.copy_path {"source":"<scratch>/drafts","destination":"<scratch>/copy"}
coda confirm
```

Expect `Gamma Beta` in both files, with the source preserved. Repeating the copy
must refuse the existing destination. Mutation responses are conversational;
raw hashes, paths and recovery details remain available through tool output.
Full revision reads are limited to complete UTF-8 text files up to 1 MiB; ranges
cannot supply an edit revision. Appending adds exactly the supplied text.

## 4. Optional move/delete checks

These actions permanently change the source. Enable destructive authority only
for this exact disposable scratch root and add `move_path` and `delete_path` to
the allowlist, then restart after that definition change. Existing read/write
grants alone must never permit these actions.

```json
"destructive": {
  "enabled": true,
  "allowed_roots": ["<scratch>"],
  "blocked_paths": ["**/.env", "**/.ssh", "**/credentials.json"],
  "require_confirmation": true
}
```

This is a fragment inside the permissions object, replacing its existing
`destructive` member; do not append a duplicate JSON key.

```text
coda mcp.filesystem_smoke.move_path {"source":"<scratch>/copy","destination":"<scratch>/moved"}
coda cancel
coda mcp.filesystem_smoke.move_path {"source":"<scratch>/copy","destination":"<scratch>/moved"}
coda confirm
coda mcp.filesystem_smoke.delete_path {"path":"<scratch>/moved","recursive":true}
coda cancel
coda mcp.filesystem_smoke.delete_path {"path":"<scratch>/moved","recursive":true}
coda confirm
```

Cancel must preserve the source/tree. Fresh approval names both move paths or the
exact recursive deletion scope. Confirmed move preserves file bytes and removes
the old name; confirmed deletion removes only `moved`. `drafts` and `report.md`
must remain. Moving across filesystems fails without a copy/delete fallback.
The configured scratch root itself is protected from move/delete. Deletion is
not a recycle-bin operation and offers no undo.

If interrupted, inspect both requested locations and any `.coda-lock`, staging
or backup paths before requesting another mutation. Some effects may already
have happened; CODA does not replay an uncertain mutation. Do not remove recovery
files on the assumption that they are stale. Operator definitions, configuration
files, running code and recognised recovery names are protected even under broad
data grants. This is application policy, not a same-user adversarial OS sandbox.

After testing, disable/remove only the new test definition and restart or refresh
its session. Inspect and remove the temporary permissions and scratch data
manually. Existing definitions/grants remain intact.

## Upgrade existing installations

Keep private `.env`, `mcp_servers/*.json` and `*.config.json` files. Supported
archive updates preserve private definitions and installed-server configuration;
do not replace them with examples. No tool, root or destructive permission is
added automatically. Existing definitions keep their exact allowlist, and an
omitted destructive section remains disabled.

Legacy explicit command/args definitions still work. To use the first-party
bundle, remove `command`, `args` and `cwd`, select `"bundled_server":"filesystem"`
and set `"enabled":true`. Preserve the ID, trust, exact allowlist, confirmations,
environment references and session choices. If the old args selected a custom
permissions file with `--config`, carry its absolute path into
`settings.config_file`. Review and add only the new tools you want, then restart.
The bundled host confirmation and operator-protection guarantees depend on the
explicit bundled identity; arbitrary external servers keep their operator policy.

Migration was exercised with a disposable legacy definition and unchanged
pre-destructive permissions: legacy listing, bundled reads/creation, old allowlist
denial, explicit new-tool opt-in and continued destructive denial all passed.
Permission bytes stayed unchanged and owned processes cleaned up. See the
[bundled lifecycle guide](mcp-bundled.md) for update/rollback details.
