# Optional browser integration: selection and validation gates

This is the evaluation/implementation contract for
[#250](https://github.com/mattordev/coda/issues/250), followed by navigation/capture
#251 and interaction approvals #252. It does not add a browser MCP tool or declare
browser containment/interaction acceptance. Ordinary HTTP reading and source
workflows work independently of this optional capability.

## Integration selection

Maintained [Playwright MCP](https://github.com/microsoft/playwright-mcp/tree/2337aff0213d28db41d3e3daf48cee19b3d0bd82)
was evaluated at the pinned source above: Apache-2.0, Node >=20, package 0.0.86,
with Playwright/core `1.66.0-alpha-2026-11-18`. Its broad scripting, evaluation,
credential/profile and network tools need stronger classification than a generic
trusted MCP allowlist. Its README explicitly excludes redirects from origin
filtering and says those filters are not a security boundary. Do not expose the
server directly as CODA's approved browser integration.

The selected candidate is a curated CODA wrapper around maintained Python
[Playwright 1.65.2](https://github.com/microsoft/playwright-python/releases/tag/v1.65.2),
Apache-2.0 and [Python >=3.10](https://raw.githubusercontent.com/microsoft/playwright-python/v1.65.2/pyproject.toml).
It works with the exercised Python 3.11.2 baseline. The Python package includes a
driver; it avoids a separately managed global Node requirement, not all Node
footprint. Browser binaries remain separate optional downloads. No upstream code
is vendored by this evaluation.

Use the SDK-matched managed Chromium headless shell as the reproducible default
candidate. The installed Edge channel remains an optional compatibility path,
without importing its user profile. In this Windows evaluation Edge
158.0.4370.33 timed out at both 15- and 45-second launch allowances; SDK cleanup
removed both owned processes/profiles. The precise Edge failure cause is not
established. Matching Chromium 159.0.8154.6 launched and passed the controlled
probe with the sandbox retained. Do not disable sandboxing or bypass enterprise
browser policy to turn a failed compatibility probe into a pass.

## Explicit, isolated evaluation setup

The [probe](../tools/browser_candidate_probe.py) installs nothing and defaults to
disabled. It imports no Playwright and creates no browser processes without
`--enabled`. It accepts no arbitrary page URLs or browser profile paths. Its only
page is fixed, controlled offline HTML. It is a developer diagnostic, not a CODA
runtime feature or proof of a complete network sandbox.

Example PowerShell commands from the repository root:

```powershell
.venv/Scripts/python.exe tools/browser_candidate_probe.py
.venv/Scripts/python.exe -m pip install --target .git/capability-programme/browser-packages -r tools/browser-evaluation-requirements.txt
$env:PYTHONPATH = (Resolve-Path .git/capability-programme/browser-packages).Path
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location).Path '.git/capability-programme/browser-binaries'
$env:PLAYWRIGHT_SKIP_BROWSER_GC = '1'
.venv/Scripts/python.exe -m playwright install chromium --only-shell
.venv/Scripts/python.exe tools/browser_candidate_probe.py --enabled --packages-dir .git/capability-programme/browser-packages --browsers-dir .git/capability-programme/browser-binaries --output .git/capability-programme/browser-evidence.json
```

Run installation only when explicitly enabling this evaluation. Choose a new
evidence filename: existing evidence is never overwritten. These commands put the
optional SDK and binaries under `.git`; they do not change root requirements,
private MCP configuration or the installed user browser. On Linux, use the
corresponding interpreter and environment syntax and install documented browser
OS dependencies explicitly. Linux native-browser validation remains outstanding.
Playwright's [browser setup documentation](https://playwright.dev/python/docs/browsers)
explains headless-only installation, custom binary directories and platform needs.

Pinned optional evaluation dependencies are recorded separately in
[browser-evaluation-requirements.txt](../tools/browser-evaluation-requirements.txt).
The Windows SDK wheel downloaded was 42.7 MB; binary installation also downloaded
the matched headless shell and small FFmpeg/Winldd support assets. Disk/network
costs vary by platform. Neither startup nor an ordinary search performs installation.

## Observed controlled Windows result

The fresh-context probe rendered JavaScript's `Rendered value 42`, captured an
800×600 PNG of 7,067 bytes, imported zero cookies, refused two HTTP routes and one
WebSocket, and delivered zero requests to its owned internal HTTP listener. It
closed four owned processes and removed the disposable profile. It used offline
mode, a deny-only proxy, blocked service workers/downloads, route rejection and
verified process ownership. Existing browser sessions were not attached to or killed.

The disabled probe reported no imported SDK and zero owned children. Unit tests
cover default opt-out, missing dependency directories without installation/launch,
and preservation of existing evidence files. These results establish a reproducible
lifecycle/render/capture baseline; they do not establish that every browser egress
mechanism is contained or that public navigation/forms work.

## Required implementation before browser acceptance

- Launch fresh nonpersistent contexts only: no existing CDP connection, profile,
  credentials, storage state, extensions or model-selected executable arguments.
- Route all page/frame/popup HTTP through CODA's DNS/peer-pinned public transport.
  Redirects and subresources need the same checks; browser `continue`/`fetch`
  fallback is forbidden. Bound aggregate requests, bytes, time and context count.
- Provide a deny-only path for background/unrouted traffic. Service-worker,
  WebSocket, QUIC, WebRTC and proxy-bypass behavior need controlled-listener tests.
  Origin filtering, isolated profiles and command-line flags alone are insufficient
  evidence of network containment. JavaScript rendering must remain supported.
- Keep arbitrary evaluation, network request, file upload and broad scripting tools
  out of the MCP surface. Classify consequential clicks, field changes and form
  submission separately from reading. Mandatory host approvals must bind one-use,
  expiring requests to the exact page, element/form, destination, method and body.
  Changed DOM/forms, navigation or grants invalidate the approval; a page script
  cannot borrow it for unrelated requests. No automatic mutation retries.
- Bound snapshot text, screenshot dimensions/bytes and private artifact storage.
  Return source URLs, observation type and partiality. Keep capture data out of
  ordinary text/model prompts; no model-selected output paths.
- Verify cancellation, failed startup, timeout, retirement, inactivity and shutdown
  close all owned browser/driver/proxy resources on Windows and Linux. Preserve
  existing user browser processes. Optional browser failure must leave HTTP,
  search and native commands available.

These are open implementation/acceptance gates, not capabilities supplied by the
diagnostic. The browser planning card remains open until the selected contract
has the required integration evidence.
