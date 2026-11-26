# Web search

The optional `search_web` MCP tool returns page titles, source URLs and search
snippets. It does not fetch or read the linked pages. A configured, user-managed
SearXNG instance is the primary provider; DDGS is the backup. Coda does not
install, start, stop or change your SearXNG service.

## Setup

1. Install the optional DDGS dependency in the Python environment running Coda:
   `python -m pip install -r integrations/coda-websearch/requirements.txt`.
   Both `mcp` (from Coda's requirements) and `ddgs` must be installed for bundle
   discovery. Nothing installs automatically at runtime.
2. Copy `websearch.config.json.example` to `websearch.config.json` beside this
   README. Set `SEARXNG_BASE_URL` in Coda's root `.env` to your instance's base
   URL, for example `http://localhost:8080` or
   `https://search.example.org/searxng`. A nonempty environment value overrides
   the legacy `search.searxng_base_url` JSON setting. When both are empty or
   absent, search uses DDGS only. Missing `max_results` and `timeout`
   default to 5 and 10; invalid values fail configuration validation.
3. Enable JSON output in your instance's `settings.yml`, under
   `search.formats` (include `json` alongside `html`). Verify it independently:
   `curl "http://localhost:8080/search?q=python&format=json"`.
   A disabled JSON format commonly returns HTTP 403.
4. Copy `mcp_servers/websearch.json.example` to `mcp_servers/websearch.json`.
   After reviewing network access, set `enabled` and `trusted` to `true` and
   `allowed_tools` to `["search_web"]`. Installation alone grants no access.
   The example's `env` mapping explicitly forwards `SEARXNG_BASE_URL` from
   Coda to the separate MCP server process; it maps variable names, not literal
   URLs. Add this mapping to existing MCP definitions when using `.env`:

   ```json
   "env": {"SEARXNG_BASE_URL": "SEARXNG_BASE_URL"}
   ```

   Keep `SEARXNG_BASE_URL=` defined even for DDGS-only use with this mapping,
   or omit the mapping entirely. Coda rejects references to missing variables.
   Restart Coda after editing `.env` so its process loads the new value.

The endpoint must be an HTTP(S) base URL without embedded credentials, query
parameters or a fragment. This version supports instances reachable without
authentication; authenticated reverse proxies require separate support.
Keep private endpoint details in the ignored root `.env` or legacy local configuration. A custom
absolute configuration path can be selected through the MCP definition's
`settings.config_file`. Local `*.config.json` and MCP definitions are preserved
by supported archive updates; templates are shipped separately.

## Requests and fallback

For a nonempty query, Coda sends one GET request to
`<base-url>/search?q=<encoded-query>&format=json`, with `Accept: application/json`.
The query is sent to your instance and its configured search engines. If the
instance times out, fails, returns malformed data or has no usable HTTP(S)
results, Coda sends the same query to DDGS once. Enabling this integration with
SearXNG therefore also permits sending queries through DDGS on fallback.
No result-page URLs are fetched.

`max_results` defaults to 5 (allowed 1–10); requested limits are clamped to this
configured cap. `timeout` defaults to 10 seconds (allowed 1–30) **per provider**.
The MCP definition's `timeout_seconds` must allow both attempts plus startup
overhead; its example uses 30 seconds for the default configuration. Increasing
the provider timeout may also require increasing the MCP timeout.

Successful structured results identify `provider` as `searxng` or `ddgs` and
retain the shared `title`, `url`, `snippet` fields. Failed searches preserve
individual provider attempts for diagnostic inspection and use Coda's existing
conversational failure handling. An unavailable primary plus an empty backup
remains a provider failure, rather than evidence that no matching pages exist.

## Verification and troubleshooting

Use `python -m ai.mcp.bundled` to inspect availability without contacting the
service. A missing optional dependency requires installing the requirements in
Coda's active interpreter; an invalid configuration requires fixing that file.
Check your endpoint and JSON support if results consistently come from DDGS.
DDGS itself can be blocked or rate limited; neither backend guarantees results.

Invoke `search_web` with `{"query": "Python documentation", "max_results": 3}`,
then try a natural-language request such as “Search the web for Python
documentation.” Check useful titles, URLs and snippets and inspect `provider`.
The regression suite mocks provider boundaries and makes no live searches;
successful live direct and natural-language checks remain separate validation.

References: [SearXNG search API](https://docs.searxng.org/dev/search_api.html),
[reference example](https://github.com/jetsonhacks/searxng-search/blob/main/tools/searxng/search_searxng.py),
[bundled MCP configuration](../../docs/mcp-bundled.md).

## Source-page reading policy

The current search tool returns snippets and does not fetch the linked pages.
The [web source selection policy](../../docs/web-source-selection.md) defines the
hybrid search/research behaviour, explicit numbered source choice,
evidence labels, bounded requests and privacy/approval boundaries. HTTP retrieval
and optional browser implementation retain their own acceptance gates; this
policy does not enable either capability or change an existing private grant.

The source workflow preserves the provider fallback while bounding queries to
1,000 characters, titles to 300, snippets to 2,000 and URLs to 4,096. It scans at
most 100 candidate entries. Search configuration is at most 64 KiB; SearXNG JSON
is at most 1 MiB plus the one-byte overflow probe. Invalid/credential-bearing
result URLs are omitted. Ordinary results are labelled as numbered search
snippets, and reading selected sources needs the separately permitted page reader.
