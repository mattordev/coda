# Bundled MCP lifecycle

Bundles are available software, with no implicit enablement, trust or tool authority.
Use `python -m ai.mcp.bundled` in Coda's active environment to inspect the static
catalog and configured instances. This launches/imports no server, resolves no
credentials and omits settings and private paths. Catalog availability, instance
enablement, process trust and connection state are separate dimensions. Per-file
errors identify invalid definitions; native commands and valid neighboring MCPs
remain usable. Restart/reconfigure after repairing configuration or dependencies.

## Opt in and migrate

Copy `mcp_servers/filesystem.json.example` to a private `.json` file. Configure
nonempty absolute `read.allowed_roots` in the server's private permissions file.
An empty root list grants no access in standalone use and prevents bundled activation.
Then explicitly select `enabled: true`, `trusted: true`, and exact permitted tool
names in `allowed_tools`. Trusted discovery with an empty allowlist grants no actions.
`confirm_tools`, dynamic permission checks and configuration retirement still apply.
Eager/lazy startup does not change these permissions.

Existing bundled definitions now default to disabled, even if already trusted.
Add `"enabled": true` yourself after reviewing permissions. Legacy `command/args/cwd`
and remote definitions retain their validation and behavior; their paths are not
rebased. A bundle ID is independent of the user instance ID. Duplicate instance IDs
retain the first valid file in sorted order; unavailable/invalid bundle definitions
report errors and do not prevent a later valid definition from using that instance ID.

Optional per-instance `settings` are strictly validated against the manifest schema.
For filesystem, `settings: {"config_file": "/absolute/private/permissions.json"}`
selects another permissions file using its existing `--config` argument. Omission
uses `filesystem.config.json` beside the server. Credentials still use `env` variable
references, resolved only at connection. Do not put credentials in settings or args.

The filesystem permissions file also accepts optional `read.workspace_roots` for
[bounded relative references](mcp-workspaces.md). These are explicit resolution
context, independent of `read.allowed_roots`; omitted/empty workspaces grant no
relative resolution and never inherit the current working directory.

## Manifest contract

Each installed `integrations/<directory>/manifest.json` describes one bundle.
The directory name need not equal its `id`; this preserves the existing owner-led
`integrations/coda-filesystem` layout. There is no master index or automatic instance.

```json
{
  "id": "example",
  "version": "1.0.0",
  "entrypoint": "server.py",
  "dependencies": ["mcp"],
  "settings_schema": {
    "type": "object",
    "properties": {"label": {"type": "string", "security": false}},
    "additionalProperties": false
  },
  "defaults": {"label": "Example"}
}
```

Required fields: `id`, `version` (three numeric components), `entrypoint`,
`settings_schema`. Optional: `dependencies`, `defaults`, `setting_arguments`,
`configuration_files`. Unknown fields and duplicate JSON keys are rejected.
IDs follow the existing lowercase instance identifier syntax. Duplicate bundle
IDs invalidate every copy, even when an earlier manifest was malformed.

Entrypoints are forward-slash relative files beneath their own bundle directory;
absolute/drive/UNC paths, traversal, symbolic links and Windows junctions are rejected.
The integrations directory, bundle directory, manifest and entrypoint must be ordinary
installed assets. Source archives must include all these assets. This is the asset-root
contract for a future packager; wheel/frozen application distribution is not supported.

Schemas use Draft 2020-12, must be self-contained (no references), and must describe
an object with `additionalProperties: false`. Only fields explicitly marked
`security: false` may have shipped scalar defaults. Roots, credentials, trust, enablement and
tool permissions must never have shipped authority. Invalid overrides reject the
instance, without falling back to defaults. Effective settings are passed as JSON
in the reserved child variable `CODA_MCP_SETTINGS` (operator env mappings cannot
override it). A standalone server can read this variable without importing Coda.

`setting_arguments` optionally maps schema property names to fixed `--long-flags`;
only string settings can become CLI arguments. No shell is used. For existing
servers with private JSON, `configuration_files` may contain objects with exactly
`setting`, `default`, `schema`: the setting selects an absolute file, otherwise
the default names a bundle-relative file. Its JSON is validated before activation.
Filesystem permissions remain enforced independently by the server; host validation
is not an OS sandbox. Check/open races against concurrent hostile link replacement
remain a limitation.

Catalog scanning checks assets and top-level Python dependency availability without
importing servers, connecting, installing packages, or reading instance permissions.
Instance configuration additionally validates settings and selected permission files.
Missing assets, interpreter, dependencies or settings produce safe inspectable errors.
Startup failures retain the existing bounded MCP diagnostics; raw stderr is discarded.

## Interpreter, dependencies and updates

Launch uses `sys.executable` and absolute script arguments, independent of cwd/PATH.
This includes updater-selected environments. Basic bundles share Coda's locked
dependencies and Python 3.11/3.12 support. No runtime pip installer or per-bundle
environment manager exists. Use explicit executables or remote MCP definitions for
servers with conflicting/heavy dependencies. Standalone invocation remains
`<configured-interpreter> <absolute-server.py> [server-specific arguments]`.

Supported archive upgrades preserve `mcp_servers/*.json` and
`integrations/*/*.config.json`, byte for byte, alongside existing `.env`, command and
wakeword preservation. External `CODA_MCP_CONFIG_DIR` and externally referenced
settings files stay external and untouched; the `.env` reference is preserved.
Local source, manifests and arbitrary other files are not copied over release assets.
Links/junctions and collisions with shipped active definitions/private config are
rejected before activation. Releases must ship `.json.example` files, never active
definitions or private configuration. New bundles/tools acquire no authority.

Activation rollback restores the original directories and original permissions.
Configuration is checked again before activation: edits, additions or removals
after staging reject that update attempt, so a newly revoked permission cannot
be replaced by an older staged copy. Prepare the update again after editing settings.
Failed updates retain recovery files; see [updater recovery](updater-recovery.md).
For manual archive replacement, back up and restore these private JSON files yourself.
Git checkouts use Git updates and never run the archive updater. Reconfigure after an
update so interpreter/assets/settings are resolved from the new installation.
