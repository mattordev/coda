# Optional browser tools

The `browser` bundle adds rendered page inspection, bounded CSS text extraction,
one viewport capture, and explicitly confirmed native form submissions. Ordinary
search and HTTP reading need no browser and remain available if this bundle fails.
Both the MCP example and browser policy default to disabled. Startup, discovery
and an ordinary search never install or launch Chromium.
The MCP schemas can be discovered without Playwright installed; an enabled
rendering call then reports a missing dependency rather than installing it.

## Explicit setup

From the repository root, using the same environment that runs CODA:

```powershell
.venv/Scripts/python.exe -m pip install -r integrations/coda-browser/requirements.txt
.venv/Scripts/python.exe -m playwright install chromium --only-shell
```

On Linux, use the corresponding interpreter and explicitly install the documented
[browser operating-system dependencies](https://playwright.dev/python/docs/browsers).
The pinned SDK uses a matching managed Chromium headless shell. It includes a
driver; a separate global Node installation is unnecessary. Browser downloads and
disk cost are optional. No user browser/profile or installed Edge channel is used.

Chromium sandboxing is required. On systems that restrict unprivileged user
namespaces, an administrator must provide a supported sandbox setup. Chromium
documents its installed Chrome sandbox helper through `CHROME_DEVEL_SANDBOX` as
an option on [Ubuntu](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md).
If using that variable for a child MCP process, explicitly reference it in the
definition's `env` mapping. Do not disable sandboxing or system policy to make a
failed launch pass. CI uses the root-owned installed helper where available;
otherwise it copies the managed binaries into root-owned storage and grants
user namespaces to that exact executable with a dedicated AppArmor profile.
It does not install a setuid helper or disable global AppArmor restrictions.

Copy `browser.config.json.example` to `browser.config.json` in this directory,
and `mcp_servers/browser.json.example` to `mcp_servers/browser.json`. Edit the new
files only; preserve existing private settings during upgrades.

For reading, set the policy's `enabled` to `true` and choose `allowed_hosts`.
An empty list grants nothing; `["*"]` explicitly permits
public hosts. Private, loopback, metadata and reserved destinations remain denied.
Enable and trust the MCP definition and set `allowed_tools` to `["inspect_page"]`.
Retain lazy/operation mode unless persistent MCP reuse is desired; every browser
operation still gets a fresh context and closes it before returning.

Restart CODA after changing definitions. No example grants forms automatically.
The policy, actual definition and current host permissions are independent checks.

## Reading and capture

Exact manual commands avoid dependence on local intent classification:

```text
coda mcp.browser.inspect_page {"url":"https://example.com/"}
coda mcp.browser.inspect_page {"url":"https://example.com/","selectors":{"heading":"h1"}}
coda mcp.browser.inspect_page {"url":"https://example.com/","capture":true}
```

Selectors select text only; they are passed as data to fixed inspection code.
There is no arbitrary evaluation, shell, browser scripting, upload or credential
tool. At most ten named selectors of 200 characters return at most 1,000 text
characters each. The ordinary snapshot has a separate text/element ceiling.

Capture is one 1000×700 viewport, not a full-page scroll. The default PNG ceiling
is 512 KiB. Image bytes are returned only in the MCP tool result, kept out of
ordinary model/history context, and never written to a caller-selected file.
CODA's text console/chat currently reports capture metadata; it is not a general
image viewer. A visual MCP client can consume the image content block. Persistent
artifact storage is zero; retained in-memory results follow the existing tool
result lifetime. This does not enable cloud image analysis.

## Confirmed native forms

Separately set `actions_enabled` to `true` in the browser policy and add
`prepare_action` and `execute_action` to the definition's tool allowlist.
Host confirmation is mandatory even if `confirm_tools` is omitted. Keep this
disabled unless a particular site/form workflow is required.

```text
coda mcp.browser.prepare_action {"url":"https://YOUR-OWN-SITE/form","form_selector":"#feedback","fields":{"message":"Controlled test"}}
```

This inspects the form, proposes an exact URL-encoded body, and submits nothing.
Use the returned fingerprint in the next command, with the same original URL,
selector and fields:

```text
coda mcp.browser.execute_action {"url":"https://YOUR-OWN-SITE/form","form_selector":"#feedback","fields":{"message":"Controlled test"},"expected_fingerprint":"RETURNED-FINGERPRINT"}
coda confirm
```

CODA names the exact destination/body before confirmation. `cancel` sends nothing.
The host accepts only a proposal from its actual configured `prepare_action`
response, bound to this instance, arguments, configuration and a 60-second expiry.
It consumes that candidate once, including on failure. Changing tools, fields,
URL, selector, grants or configuration cannot transfer approval. A new inspection
is necessary after expiry, retirement or an attempted action.
The copied fingerprint includes a bound expiry. CODA checks it during re-inspection
and the final network operation as well as when accepting the confirmation.

After confirmation a new disposable browser re-inspects the form. Its observed
DOM, controls, hidden values, destination, requested values and policy must match
the fingerprint. Dynamic nonces can therefore require a fresh inspection; they
are not silently updated under an earlier approval.

The supported action is one native POST form, optionally selected through its
submit button. CODA builds and sends the exact approved body itself. It does not
type into the page, run change/click listeners or hand page scripts network
authority. GET forms, multipart forms, files, passwords, duplicate control names,
changed hidden/submit fields, submit-button destination overrides and custom
JavaScript-only widgets are refused. Login-dependent sites are unsupported:
browser cookies, credentials and storage state are neither imported nor forwarded.

No mutation is retried, and no POST redirect is followed. An HTTP response is
reported as an observation; it does not prove that an order, message or other
business action succeeded. A timeout/cancellation/failed response may leave an
unknown external effect. Inspect the destination before requesting a new action.
Do not use purchases or real destructive forms as tests.

## Network and resource limits

Chromium runs offline, with a deny-only local proxy, blocked service workers and
WebSockets, no downloads, disabled QUIC/DNS prefetch/direct WebRTC UDP, and no
browser DNS fallback. All permitted GET resources are fulfilled with CODA's
DNS/peer-pinned public transport. No browser headers, cookies, authorisation,
proxy environment or request bodies are forwarded. HTTPS retains certificate and
hostname validation. Browser errors never trigger an unrestricted transport.

CODA resolves initial redirects before navigating Chromium to the final URL,
preserving the final origin. Playwright does not intercept every redirect hop;
redirecting subresources are therefore refused rather than continued. Additional
frame/popup/scripted document navigation is blocked. Unsupported or blocked
resources make the result explicitly partial. Some sites will not render fully.

GET-only retrieval assumes the remote server follows HTTP semantics. It cannot
prove that an arbitrary website's GET handler has no side effects. The controls
prevent arbitrary browser mutation methods and unapproved form submissions;
they are not an OS-level sandbox against Chromium vulnerabilities.

Defaults per operation: 25 seconds total, 24 requests, 2 MiB decoded aggregate,
4 MiB wire aggregate, 12,000 text characters and 20 element observations. Per
resource, the existing HTTP limits also apply. A partial/truncated resource is
discarded before execution. Limits can be reduced in private configuration;
supported upper bounds are validated in `BrowserLimits` and `NetworkPolicy`.
SDK/browser startup, failed navigation, cancellation and shutdown own their
resources. Existing user browser processes are never attached to or killed.

## Validation

`tests.test_browser_session`, `tests.test_browser_actions` and
`tests.test_browser_mcp` cover bounded authority, default opt-out, exact proposals,
expiry/replay/configuration changes, captures withheld from model context and SDK
discovery without a browser launch. `tests.test_public_http` retains adversarial
DNS/peer/redirect/byte/cancellation checks and adds exact-body POST/no-retry cases.

Explicitly installed Chromium can run the real fixture:

```powershell
$env:CODA_RUN_BROWSER_SMOKE = '1'
.venv/Scripts/python.exe -m unittest tests.test_browser_integration -v
```

The fixture maps a checked public peer to its own loopback listener only in test
code. Production has no private-IP override. It exercises real Chromium and MCP
in both modes, rendering, redirects, capture, denied internal HTTP/WebSocket/
WebRTC attempts, unapproved POSTs, exact approved POSTs, changed forms, and owned
process/profile cleanup. No public site receives a test submission. Optional
browser CI runs separately on Windows/Linux Python 3.11/3.12; default tests skip
native rendering unless explicitly opted in. A skipped native test is not a pass.
