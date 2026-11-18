# v1.5.5 - Websearch MCP & Conversational MCP Errors

This release adds opt-in web search through an existing SearXNG instance with
DDGS as backup, and keeps MCP tool failures conversational across text and voice.
It builds on the v1.5.4 runtime and streaming improvements.

## Changes

- The bundled `search_web` tool returns bounded page titles, source URLs and
  snippets. A configured SearXNG instance is tried first; DDGS is tried once
  when the instance fails, times out or returns no usable results. Without a
  configured instance, search uses DDGS directly.
- Both providers return the same result fields. Structured successful results
  identify the provider used; failed searches retain individual provider attempts
  for diagnostic inspection. A backend failure is not described as proof that
  no matching information exists.
- Search configuration supplies defaults for result limits and timeouts, checks
  invalid values and rejects invalid instance URLs. Configuration examples and
  optional dependency instructions support explicit setup without automatically
  enabling the integration.
- MCP failures use deterministic conversational messages for unavailable tools,
  timeouts, empty results, permission failures and invalid arguments. Technical
  provider errors remain available through explicit diagnostics rather than
  ordinary replies, spoken output or future conversation history.
- Existing successful-result interpretation and actionable filesystem workspace
  clarification remain in place. Search does not change tool trust, allowlists,
  confirmation, privacy policy or cancellation permissions.
- Patch OpenTelemetry API to 1.45.2 for the reported traceparent-parsing denial
  of service advisory. No provider or telemetry configuration change is required.

The shared failure handling was implemented in
[#228](https://github.com/mattordev/coda/pull/228). Web-search delivery is tracked
in [#227](https://github.com/mattordev/coda/issues/227), with external SearXNG
support in [#229](https://github.com/mattordev/coda/issues/229) and the original
search request in [#33](https://github.com/mattordev/coda/issues/33).

## Configuration

- Install the optional search dependency in Coda's active Python environment:
  `python -m pip install -r integrations/coda-websearch/requirements.txt`.
- Copy the web-search configuration and MCP definition examples as described
  in the [web-search setup](../integrations/coda-websearch/README.md). Explicitly
  enable and trust the server and allow `search_web` after reviewing network access.
- Set `SEARXNG_BASE_URL` in `.env` and forward it through the MCP definition's
  `env` mapping. A nonempty environment value takes precedence over the legacy
  `search.searxng_base_url` JSON field. Leave both empty for DDGS-only search.
- Enable JSON output in your SearXNG instance's `search.formats`. A working HTML
  search page alone does not establish that JSON requests are enabled.
- `max_results` defaults to 5, with an allowed range of 1–10. `timeout` defaults
  to 10 seconds per provider, with an allowed range of 1–30. Allow enough MCP
  request time for both attempts and process startup; the example uses 30 seconds.

Restart Coda after updating or changing `.env`. Retain existing private settings,
MCP definitions and workspace permissions. Existing DDGS-only configurations
continue to work without a SearXNG URL.

## Validation and limits

Regression coverage includes successful and malformed provider responses,
timeouts, empty results, fallback ordering, result caps, URL encoding, environment
precedence and safe conversational failure handling. The supported test matrix
covers Windows and Linux with Python 3.11 and 3.12. The release tag is checked
against `version.json` with `python -m utils.update_releases v1.5.5`.

The owner confirmed successful live search against an existing SearXNG instance.
Automated provider tests use mocked boundaries; they do not guarantee availability
of every search engine or verify live microphone and speaker behavior. DDGS and
the engines behind SearXNG may be blocked, rate limited or unavailable.

Search returns snippets; it does not fetch or read the linked pages. Coda does
not install, launch, stop, update or remove SearXNG in this release. The instance
remains user-managed, and authenticated reverse proxies are not supported by
this integration. Managed service setup and actual page reading remain separate
follow-ups, with no future release commitment.

See [web-search setup](../integrations/coda-websearch/README.md),
[MCP feedback](mcp-feedback.md) and [bundled MCP configuration](mcp-bundled.md)
for detailed behavior and setup requirements.
