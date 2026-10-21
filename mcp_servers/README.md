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
