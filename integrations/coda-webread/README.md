# Public webpage reader

`read_url` retrieves one permitted public HTTP(S) page and extracts bounded text,
title, description, language, final URL, observation time, and a bounded link list.
It uses the existing Python environment and does not require a browser or paid
service. Search remains a separate capability and does not implicitly fetch pages.

Copy `webread.config.json.example` to `webread.config.json`, enable its network
section, and set `allowed_hosts` to the exact public hosts you intend to read.
For a first test, use `["example.com"]`. Redirect destinations must also be
allowed. `"*"` explicitly permits public hosts; private and special destinations
are still refused. See [the network boundary](../../docs/web-network-boundary.md).

Copy `mcp_servers/webread.json.example` to `mcp_servers/webread.json`. Set
`enabled` and `trusted` to `true`, and `allowed_tools` to `["read_url"]`. Restart
CODA or rediscover MCP tools through the existing runtime controls. These are
operator steps: installing the bundle does not change existing permissions.

Test with:

```text
coda mcp.webread.read_url {"url":"https://example.com/","max_chars":1000,"max_links":3}
coda show tool output
```

The limits in a request can narrow the operator's text/link ceilings. They cannot
increase them. Network settings bound total elapsed time, decoded response bytes,
HTTP wire bytes (including headers), redirects, and allowed ports. Gzip decoding
also respects the byte ceiling. Changing or deleting configuration while a request
is running causes its result to be discarded. The tool performs a GET and returns
observations; it never executes page instructions or automatically follows links.

HTML extraction prefers `main`, then `article`, then body text. Scripts, inline
styles, navigation, footers, embedded frames and explicitly hidden elements are
omitted. This lightweight parser does not compute CSS, execute JavaScript, or
implement a browser's layout or full HTML repair rules. Some sites consequently
return empty or imperfect text. Plain text, Markdown, and JSON are also supported;
PDFs and other binary formats are refused. Supported encodings are UTF-8, UTF-16,
Windows-1252, Latin-1 and ASCII. An incomplete multibyte suffix at a byte ceiling is
omitted. Byte-limited, character-limited and server-partial responses are labelled.
Markup tokens have an independent 8 KiB ceiling, with a 128-element nesting and
128-attribute ceiling, so limits do not depend on Python patch-version HTML repair.

Explicit requests to summarise use CODA's existing privacy-controlled, read-only
interpretation path. A title, search snippet, or empty response never substitutes
for an article body. Page instructions are untrusted observations, not tool grants.

## Integration choice

Maintained MCP Fetch was evaluated at source commit
`c799c8552b0f27b7a33a7b9233773da144dddcf5` (package metadata 2026.11.18, MIT).
Its Python minimum is 3.11.5, above this repository's exercised 3.11.2 environment;
its returned content/prefix pair does not expose the final URL or caller-controlled
wire/redirect ceilings required here. CODA therefore uses its own small transport
and extractor, with the existing `mcp`, `idna` and `certifi` dependencies. No Fetch
code was copied, package installed, or browser fallback enabled. The pinned source
evaluation is distinct from testing an installed Fetch package.

Source: [MCP Fetch](https://github.com/modelcontextprotocol/servers/tree/c799c8552b0f27b7a33a7b9233773da144dddcf5/src/fetch).

## Verification

`tests/test_web_extract.py` checks grounded metadata, text selection, links,
encodings, inert embedded resources, and extraction ceilings.
`tests/test_webread_mcp.py` checks fail-closed configuration, argument ceilings,
permission revocation, safe failures, and actual SDK discovery/denial/cleanup in
operation and persistent modes. Neither suite needs public network access.
`tests/test_public_http.py` separately exercises the actual HTTP protocol with
controlled fixtures, redirects, cancellation, decompression, and DNS/peer checks.

A separate opt-in native run fetched the public Python `html.parser` documentation
through the actual bundled SDK transport in both operation and persistent modes.
Each returned HTTP 200, the final source URL and real title, 999 characters of
extracted text and explicit character-limit partiality; owned MCP processes were
retired afterwards. This used temporary permission files and did not modify the
operator's private configuration. It is retrieval evidence, not browser or full
web-release acceptance evidence.
