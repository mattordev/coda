# v1.5.7 - Web Capabilities

This release adds bounded public-page reading and source-grounded research,
alongside optional disposable browser tools. Ordinary searches stay lightweight:
their spoken replies summarise snippets, with full source links retained in tool
output. Existing private settings and explicit grants are preserved.

## Changes

- Preserve existing SearXNG/DDGS search, with short spoken overviews rather than
  spoken numbered URLs. Simple searches fetch no result pages.
- Read public HTTP(S) text without a browser. Retain source/final URLs, title,
  metadata, timestamps, bounded links and explicit partial/failure observations.
- Use the hybrid research policy: rank bounded source candidates for the question,
  read a small number of relevant pages, or follow explicit selected sources.
  Query-focused extraction can recover evidence beyond a long page prefix.
- Ground fetched-page replies in literal retrieved evidence selected through
  validated IDs. Invalid/free-form factual output is rejected; insufficient
  evidence abstains. This deliberately limits paraphrasing and does not verify
  that a source's claims are true.
- Retain original source numbering and provenance for follow-ups. Single-use
  offers can read an observed source through normal grants. Stop preserves
  completed sources until their original expiry, while cancelling pending actions.
- Add an opt-in browser bundle for JavaScript-rendered inspection, named CSS text
  extraction and one bounded viewport PNG. Fresh contexts import no profiles,
  cookies, credentials or sessions and close before returning.
- Broker browser resources through CODA's checked public transport. Initial
  redirects preserve the final origin; redirecting subresources, extra document
  navigation, direct network paths, service workers, WebSockets and unapproved
  mutation methods are refused. Blocked content is labelled partial.
- Add inspected native form proposals with mandatory host confirmation. Bind
  exact page/form/fields/destination/body and current configuration to one
  expiring use, then re-inspect before sending. Page code never receives approval.
  No POST retry or redirect is performed; HTTP responses are not business-outcome
  verification. Passwords, files, unsupported forms and custom script widgets
  remain unavailable.
- Provide request-owned acknowledgement, action and progress speech, with matching
  chat text when spoken. Provider fallback is announced after multiple actual
  failures. Slow local models can use the operator's configured interpretation
  allowance rather than a fixed 20-second cap.
- Isolate dashboard write failures from recognition and valid answer speech, use
  unique staging files and bounded replacement retries, and preserve the last
  valid state on persistent failure.
- Correct Windows append/edit security staging on Modify-only workspaces without
  relaxing DACL/ownership preservation, revision checks or confirmation.

## Setup and migration

Search, HTTP reading and browser rendering have independent definitions and
policies. Adding a bundle grants nothing. Preserve `.env`, active `mcp_servers`
JSON and integration `*.config.json` files; copy new examples only when explicitly
setting up that capability. Upgrade and rollback tests preserve reader/browser
policies byte-for-byte, including deliberately disabled browser/action settings.

- [Existing web-search setup](../integrations/coda-websearch/README.md)
- [Public reader setup and limits](../integrations/coda-webread/README.md)
- [Hybrid research and source follow-ups](web-source-selection.md)
- [Optional browser installation, forms and limits](../integrations/coda-browser/README.md)
- [Shared public network boundary](web-network-boundary.md)
- [Runtime voice feedback and dashboard isolation](concurrent-runtime.md)

Ordinary search/reading use the existing Python environment. Playwright and its
matched managed Chromium are separate explicit installations; no startup or
search installs them. If an upgrade creates a fresh Python environment, explicitly
reinstall optional browser dependencies there before using enabled browser tools.
Sandbox-capable OS setup is required; there is no unsandboxed fallback.

## Safe checks

With separately permitted reader and search instances:

```text
coda search the web for Python HTMLParser documentation
coda read the first source
coda research site:docs.python.org HTMLParser limitations
coda mcp.webread.read_url {"url":"https://example.com/","max_chars":1000}
coda show tool output
```

With the optional browser explicitly configured:

```text
coda mcp.browser.inspect_page {"url":"https://example.com/","selectors":{"heading":"h1"}}
coda mcp.browser.inspect_page {"url":"https://example.com/","capture":true}
```

Capture bytes are image content in the MCP result, with no persistent artifact
storage or ordinary model/history context. CODA's current text UI reports the
capture; viewing the image requires a visual MCP client. A screenshot does not
enable cloud image analysis.

For voice, ask an ordinary search and then a research question. Check that status
speech appears in chat, URLs are not read aloud, snippet/page scope is honest,
and stopping preserves completed source references without executing a pending
action. Model loading and speech-provider latency remain separate from tool time.

Form tests belong on owned disposable fixtures, never purchases or destructive
real forms. The integration fixture exercises exact approved POST, cancellation,
replay, expiry and changed-form refusal without public submissions. An interrupted
POST may have an unknown effect; check the destination before a new request.

## Validation and limits

Core regression remains Windows/Linux Python 3.11/3.12. A separate optional-browser
matrix explicitly installs the pinned SDK/browser and exercises actual Chromium,
HTTP and MCP in both session modes. Default tests skip native browser work unless
opted in. Unit/mocked, real fixture integration, native public retrieval and
local-model evidence are recorded separately on [#270](https://github.com/mattordev/coda/issues/270)
and the [release card](https://github.com/mattordev/coda/issues/272).

Controlled tests cover private/mixed/rebinding DNS, peer pinning, TLS, redirects,
byte/time budgets, cancellation, configuration changes, denied browser methods,
internal HTTP/WebSocket/WebRTC attempts, capture limits, exact forms and owned
process/profile cleanup. Public Python-documentation retrieval/rendering and the
controlled local llama.cpp evidence-selection workflow were exercised separately.

GET-only browsing assumes remote HTTP semantics; it cannot prove that arbitrary
servers never mutate on GET. Network controls are not an OS sandbox against
browser vulnerabilities. Redirect-heavy, authenticated and custom-widget sites
can fail or return partial observations. General PDF extraction, browser login,
arbitrary scripts, background execution, scheduling and general tool composition
are not supplied by this release.
