# MCP server definitions

Place one JSON definition per server here. Local `*.json` files are ignored
by Git. The `.json.example` template is not discovered, and no process starts
merely because a definition exists. See [Local MCP](../docs/mcp-local.md),
[Remote MCP](../docs/mcp-remote.md) and the
[implementation walkthrough](../docs/mcp-framework-walkthrough.md).
`example.json.example` and `remote.json.example` are safe templates with trust
disabled and empty action allowlists. Copy only the template you need to a new
`.json`, supply your own paths/endpoint, and explicitly review trust/permissions.

## Bundled filesystem server

`filesystem.json.example` selects Coda's first-party filesystem MCP using
`"bundled_server": "filesystem"`. The active interpreter and installed script
path are resolved by Coda, independent of cwd. This is an explicit selection,
not automatic discovery or enablement. Trust and exact tool permissions still
default to deny. See the [filesystem setup](../integrations/coda-filesystem/README.md)
and [feedback behavior](../docs/mcp-feedback.md).

For the complete read/search/edit/copy/move/delete capability set, use the
[filesystem safe test route and migration guide](../docs/filesystem-quickstart.md).
Newly discovered tools do not gain allowlist entries or filesystem grants on
upgrade. All bundled filesystem mutations require action-bound confirmation;
move/delete additionally need independent destructive authority. Operator
definitions and declared bundle configuration files remain protected even when
a broad data grant overlaps them.

## Web search

`websearch.json.example` is an opt-in template for SearXNG search with DDGS as
backup. See [web-search setup](../integrations/coda-websearch/README.md) for
optional dependencies, instance configuration and explicit permissions.

## Bundled lifecycle

See [bundled MCP setup, schemas and migration](../docs/mcp-bundled.md).
Bundled definitions require `enabled: true` independently of trust and tool permission.
