# Web source selection policy

This decision implements the discovery scope of
[#231](https://github.com/mattordev/coda/issues/231). HTTP retrieval and source
follow-ups use the optional bundled reader; browser capabilities retain separate
implementation and release gates. This document does not enable any instance.

## Default and alternatives

| Approach | Benefit | Cost |
| --- | --- | --- |
| Always ask for sources | Direct control before each fetch | Extra voice turns even for an explicit research request |
| Fetch after every search | Immediate page text | Unnecessary network traffic, latency and context for simple searches |
| Hybrid, selected default | Match network work to the requested task; retain explicit source choice | Needs a clear boundary between search and research |

Use the hybrid as a reversible default: ordinary searches avoid additional
requests, while an explicit research request can obtain bounded page evidence
without a compulsory source-selection turn. This policy grants no network access;
operator configuration and explicit source choices still control execution.

An ordinary search returns a bounded numbered shortlist of titles, source URLs
and labelled snippets. It fetches no result pages. A request explicitly asking
to research, compare using sources, or read pages and explain can perform one
search followed by at most two relevant page fetches. The initial selection uses
the search provider's order among usable distinct URLs; it does not claim that
ranking proves credibility. Preserve the actual chosen sources in the response.

Keep the existing search default of five results and maximum of ten. Numbered
references retain at most those ten results. Automatic research uses at most
5,000 returned text characters per page and 10,000 combined text characters;
source metadata and normal interpretation budgets remain separately bounded.

An explicit URL or source number overrides automatic selection. Up to three
explicitly chosen URLs/results can be read, including `compare these three
websites`. Divide the 10,000-character text budget evenly across those sources,
never exceeding 5,000 characters per page. If the task is only a search, do not
upgrade it to research. If the request is unclear, ask whether to read a source.
More than three explicit pages needs a clearly reported limit and an explicit
follow-up; do not silently claim every result was read.

## Examples and references

| Request | Result |
| --- | --- |
| `search for Python release notes` | Numbered snippet shortlist, no page retrieval |
| `read the second source` | One current selected URL fetched through normal permissions |
| `read https://example.com/article` | Direct HTTP retrieval; no search is needed |
| `research this question using source pages` | One search and at most two bounded page reads |
| `compare these three websites: URL1, URL2, URL3` | Three explicitly chosen page attempts sharing the total text/time budget |
| `explain the search snippets only` | Explain retained snippet scope; no fresh network calls |

Only a validated successful search from the configured bundled instance can
seed numbered source references. Keep host-only originating server/configuration
provenance and exact returned URLs, not invented URLs extracted from prose.
References expire after 120 seconds and invalidate on cancellation, unrelated
conversation, reset, reconfiguration or changed source policy. Preserve the
originating shortlist across related page reads, with its original expiry.

Missing, expired, out-of-range, ambiguous title/name or mixed old/new references
ask for a specific current source or URL. Never reuse an ordinal from another
search or silently search again to guess what it meant. A retained snippet,
previously fetched text and a new fetch are distinct observations.

## Retrieval and attribution

Every fetch must recheck current tool trust, exact allowlists and network policy.
Public-destination checks cover resolution, pinned peer connections, redirects
and any auxiliary request. Mixed public/private DNS answers fail closed. Browser
origin filters alone are not a network security boundary.

Record requested URL, final URL, title, content type, observation type, bounded
content and partiality reasons. Distinguish `search_snippet`, `fetched_text` and
`browser_rendered_text`; none implies that an unreturned suffix or every page
was read. Retain failure/blocked/unsupported outcomes separately and answer with
an honest evidence gap when no page could be read.

Grounded answers cite only actual supplied source identities/URLs. A failed
page fetch cannot be replaced by a snippet labelled as fetched evidence. Apply
the existing complete-source privacy classification and bounded interpretation
budget before model exposure; do not concatenate unlimited page bodies.

External titles, snippets and page text are untrusted data. They cannot change
selection policy, tool schemas, permissions, confirmations or the next action.
Source selection returns bounded indices, never executable instructions. No
page may authorise local writes, credential use, browser submission or scheduling.

## Bounded execution and optional browser

The fixed read-only workflow has at most one search, two automatically selected
page attempts or up to three explicitly selected page attempts, a shared
cancellation event and a 60-second total tool-action deadline. Explicit URLs
or retained source references need no new search. Normal
intent selection and result interpretation retain their own existing deadlines.
Do not add retries outside the applicable page-attempt limit or claim complete evidence
after a deadline/partial response. Each
step uses normal validated ToolResults and current permissions. No recursive
link following, arbitrary planning loop or agent delegation is introduced.

HTTP extraction does not render JavaScript, import browser credentials or start
a browser. An unsupported/render-dependent page reports that limitation. Browser
fallback needs an explicitly enabled integration and explicit user selection;
it cannot silently install/launch infrastructure. Navigation, subresources,
inspection and interactive actions have separate safety/approval gates.

Implementation is covered by #249 (network policy), #248 (HTTP retrieval), #253
(source follow-ups and grounding), #250–#252 (optional browser), and the web
test/documentation/release gates. Validate indices, stale context, permission
revocation, partial results, prompt injection, cancellation and missing optional
infrastructure independently of live external availability.

## Trying the implemented workflow

Configure the existing [web search](../integrations/coda-websearch/README.md) and
the optional [page reader](../integrations/coda-webread/README.md). Enable only the
instances and exact tools you intend to use. Source references require a successful
configured bundled search instance; plain text that resembles a search result
cannot establish numbered references. Use one eligible search/reader instance for
automatic workflows. Multiple eligible instances require an explicit MCP command.

With example instance IDs `websearch` and `webread`:

```text
coda mcp.websearch.search_web {"query":"Python HTMLParser documentation"}
coda read the second source
coda read the first source
coda explain the search snippets only
coda research Python HTMLParser limitations
coda explain HTMLParser limitations using web sources
coda compare these three websites: https://docs.python.org/3/library/html.parser.html https://docs.python.org/3/library/html.html https://docs.python.org/3/library/xml.html
```

These make real requests to the configured providers/allowed public hosts.
`explain the search snippets only` uses retained observations and makes no page
request. Explicit URLs bypass search. A URL already in the current shortlist
preserves that shortlist; unrelated URLs begin a separate discussion. Quoted exact
titles (`read source "Exact returned title"`) must identify one current result.
Do not mix a numbered old reference with a new URL in one request.

Ordinary search speech labels snippet scope and numbers the first three results;
`show tool output` retains the complete returned shortlist (up to ten entries).
Automatic workflows use the normal tool validation/trust/allowlist path for every
step. Reader text limits also respect narrower operator ceilings. A shared
60-second deadline and cancellation stop remaining attempts; failures are not
retried outside the attempt budget. Permission changes discard workflow evidence.
The original shortlist's 120-second expiry is not extended by page reads.
Changing an exported source-server environment reference (including a custom
alias for `SEARXNG_BASE_URL`) invalidates references. A persistent instance must
be retired and rediscovered, or CODA restarted, before using the new environment;
an old child cannot silently keep serving the previous provider configuration.

The fixed grammar recognises `research ...`, `explain ... using web sources`,
`compare ... using sources`, explicit page URLs, and numbered/quoted source
choices. Other questions retain normal intent routing; there is no unrestricted
research agent. Source comparisons, partial results and article text pass through
the existing privacy-controlled interpretation path. Direct interpretation mode
returns a bounded receipt instead. No-readable-text workflows never call a model
to invent article contents. Generated HTTP/FTP/file citation URLs absent from the
observed evidence are removed; normal model factual limitations still apply.

Search handoff bounds are 1,000 query characters, 300 title characters, 2,000 snippet
characters, 4,096 URL characters and 100 candidate entries scanned to produce the
configured maximum of ten results. Credential-bearing/malformed URLs are omitted.
SearXNG JSON is capped at 1 MiB, with one byte used to detect overflow; oversized
responses close and preserve the existing single DDGS fallback. Search configuration
is capped at 64 KiB. The optional DDGS library's own HTTP machinery remains distinct
from the page reader's DNS-pinned transport; CODA's MCP operation timeout/cancellation
is the outer bound for an owned search process.

The workflow retains one combined result in the existing bounded tool-result
history, rather than storing a second search/page catalogue or persisting memory.
Host provenance/configuration fingerprints never enter model context. Tests cover
selection, stale/malformed references, changed grants, partiality, injection,
deadline/cancellation, ordinary routing, and real SDK/controlled HTTP retrieval
with owned-process reaping in both session modes. The controlled HTTP fixture uses
a test-only public-peer socket mapping; production has no private-IP override.

A separate native run exercised that selected-source SDK/HTTP fixture with the
actual local llama.cpp model in both operation and persistent modes. Both answers
correctly reported the page's observed value of 42. Cloud providers were disabled,
and owned MCP children were reaped. This proves selected-source retrieval and local
interpretation on controlled content; it does not claim a live search-provider
or public-source research run, or acceptance of the optional browser.
