# Web source selection policy

This decision implements the discovery scope of
[#231](https://github.com/mattordev/coda/issues/231). HTTP retrieval, source
follow-ups and optional browser capabilities retain their own implementation
and release gates; this document does not enable them.

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

An explicit URL or source number overrides automatic selection. If the task is
only a search, do not upgrade it to research. If the request is unclear, ask
whether to read a source. More than two requested pages needs a clearly reported
limit and an explicit follow-up; do not silently claim every result was read.

## Examples and references

| Request | Result |
| --- | --- |
| `search for Python release notes` | Numbered snippet shortlist, no page retrieval |
| `read the second source` | One current selected URL fetched through normal permissions |
| `read https://example.com/article` | Direct HTTP retrieval; no search is needed |
| `research this question using source pages` | One search and at most two bounded page reads |
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

The fixed read-only research path has at most one search and two page requests,
a shared cancellation event and a 60-second total tool-action deadline. Normal
intent selection and result interpretation retain their own existing deadlines.
Do not add retries outside the two page-attempt limit or claim complete evidence
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
