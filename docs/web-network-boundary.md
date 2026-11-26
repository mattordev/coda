# Public web retrieval boundary

`utils/public_http.py` provides a bounded HTTP(S) GET transport for the web
capability release. It imports no CODA runtime and starts no network operation
at import. It is a transport primitive, not an enabled MCP integration or a
network grant. The HTTP reader and optional browser retain separate acceptance
gates and must check their current operator configuration before using it.

## Integration decision

The maintained [MCP Fetch reference source](https://github.com/modelcontextprotocol/servers/tree/c799c8552b0f27b7a33a7b9233773da144dddcf5/src/fetch)
was evaluated at the pinned revision. Its metadata declares version
`2026.11.18`, MIT, MCP `>=2.3,<3` and Python `>=3.11.5`. Its public fetch helper
returns content/prefix text; adapting configurable wire/decoded/redirect limits
and structured final-URL evidence would require lower-level internal APIs.
The current local baseline is Python 3.11.2. No reference package was installed,
vendored or treated as proof that a published wheel matches the inspected source.

Use a small standard-library transport with CODA-owned destination policy and
typed response metadata. This preserves the existing interpreter baseline,
does not require a browser/Node/extraction service for ordinary reading, and
provides a reusable public-network boundary for browser resource brokering.
The trade-off is owning this security-sensitive transport and its adversarial
tests. IDNA and Certifi are already pinned core dependencies: IDNA 2008/UTS 46
normalisation avoids legacy hostname substitutions, and CA data supplements
system roots without another installation or a global trust-store change.

## Destination and connection checks

- Accept bounded absolute HTTP(S) URLs with no embedded credentials, control
  whitespace, backslashes, encoded hosts or scoped IPv6 forms. Normalise IDNA
  hostnames and encode resource paths/queries; fragments are not transmitted.
- A default `NetworkPolicy()` permits no host. An explicit host tuple controls
  exact hosts; `*` explicitly permits public hosts. Default ports are 80/443.
  Neither a host entry nor a nonstandard allowed port can grant private access.
- Reject literal private/loopback/link-local/reserved/multicast/shared addresses
  and local/reserved hostname forms before connecting. Numeric/hex/octal host
  variants still undergo resolution and address checks.
- Inspect the entire bounded DNS answer set. Any unsafe answer, inconsistent
  address family or excessive set refuses the request; do not choose a public
  answer while silently ignoring a private one.
- Connect directly to the checked numeric address using the matching socket
  family, with no second hostname lookup. Check the actual peer before sending
  HTTP. For TLS, retain the original hostname as SNI and verify certificates and
  hostname using system roots plus the existing Certifi CA bundle. No insecure
  TLS fallback is provided.
- Revalidate every redirect with fresh DNS/peer checks, exact host/port policy
  and the shared deadline/byte budget. Refuse HTTPS-to-HTTP downgrade. Close
  redirect bodies without draining them; preserve requested and final URLs.
- Read no proxy environment, cookie jar, netrc credentials or browser profile.
  Requests send fixed headers and no ambient authentication/cookies. The
  transport makes no robots, image, script or other auxiliary requests.

Address classification is deliberately conservative across supported Python
patches. In addition to `ipaddress` global/multicast/reserved checks, IPv4
`192.0.0.0/24` and `192.88.99.0/24` are excluded. Native IPv6 must be within
`2000::/3`, excluding `2001::/23`, `2001:db8::/32`, `2002::/16` and `3fff::/20`.
Mapped/translation/6to4 and other special-purpose forms cannot conceal an
internal IPv4 endpoint. Some specialised globally reachable assignments are
therefore refused. Sources: [Python ipaddress](https://docs.python.org/3.11/library/ipaddress.html),
[IANA IPv4 special-purpose registry](https://www.iana.org/assignments/iana-ipv4-special-registry/iana-ipv4-special-registry.xhtml),
[IANA IPv6 special-purpose registry](https://www.iana.org/assignments/iana-ipv6-special-registry/iana-ipv6-special-registry.xhtml),
[Python TLS contexts](https://docs.python.org/3.11/library/ssl.html#ssl.SSLContext.wrap_socket)
[IDNA normalisation](https://github.com/kjd/idna#compatibility)
and [Certifi CA data](https://github.com/certifi/python-certifi#usage).

## Resource and lifecycle limits

| Resource | Default | Maximum |
| --- | --- | --- |
| Exact allowed hosts | None | 64 |
| Allowed ports | 80, 443 | 16 explicit ports |
| URL | 4,096 input characters | 8,192 encoded characters |
| Total transaction deadline | 10 seconds | 30 seconds |
| DNS answers | Entire set checked | 32 |
| Outstanding DNS lookup | One | One |
| Decoded response body | 512 KiB | 2 MiB |
| HTTP response wire budget | 1 MiB | 4 MiB |
| Redirects | Three | Five |

The response wire counter includes HTTP headers, chunk framing and encoded body
across redirects, after TLS decryption; it excludes TLS/TCP framing. Identity and
single gzip streams are supported. Inflation is bounded to the remaining decoded
budget plus one probe byte. A body over its decoded cap returns a bounded prefix
with `partial=True` and closes the connection. Wire exhaustion, incomplete HTTP/
gzip, unsupported encodings and HTTP errors fail without claiming complete text.
Remote error bodies are not quoted or read whole to explain a failure.

Every socket read uses the remaining absolute deadline, so a slow drip cannot
reset an inactivity timeout forever. DNS runs in one daemon worker; its caller
can time out/cancel without allowing a later connection. Until the OS resolver
finishes, another lookup gets a bounded busy failure instead of accumulating
workers or queued queries. No resolver result is cached as future authority.
Blocking native socket/TLS/OS work is also subject to the consumer's MCP transport
timeout and owned-process cleanup; this is not a hard real-time OS sandbox.

The optional caller validation callback runs throughout the transaction and
before returning observations. A changed/revoked policy discards the result.
Cancellation closes the owned connection. Response bytes/headers remain untrusted
data: this layer does not execute HTML/scripts, interpret instructions or choose
another tool. Extraction/grounding must preserve the
[source evidence policy](web-source-selection.md).

## Validation evidence

Focused tests distinguish address/connection mocks from actual HTTP fixture
traffic. Controlled loopback servers use an injected test dialer that maps a
checked public address to the owned fixture and reports that test peer identity.
Production exposes no private-IP override. Tests prove bytes/metadata, no HTML
auxiliary requests, absolute slow-drip deadlines and cancellation/connection
closure without contacting unrelated internal services.

Separate unit cases cover private/encoded/mixed DNS, rebinding between redirects,
numeric pinning, unexpected peers, SNI/verified TLS, exact host/port policy,
malformed inputs, IPv6/IDNA, byte caps, gzip bombs and incomplete/chunked streams,
bounded DNS workers, current permissions and content-safe failures.

Native public HTTPS was exercised against example.com, Python documentation and
GitHub using verified TLS. The first system-only example.com attempt failed with
a missing issuer; supplementing the existing CA bundle succeeded without changing
verification requirements. The final transport returned example.com's real 200
response and complete bounded body; larger source pages returned labelled prefixes.
This evidence does not claim browser isolation or a completed web MCP release.

The [optional page reader](../integrations/coda-webread/README.md) now consumes this
transport. Its own network/configuration and MCP allowlist gates default to off;
the transport itself grants no access. Browser request routing remains a separate
integration with its own containment requirements.
