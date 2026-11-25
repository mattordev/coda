# Functional capability expansion: audit and implementation boundaries

Audit date: 25 November 2026. CODA baseline: `1317a16` (v1.5.5 release
promotion); integration branch: `develop`. Findings below distinguish released
code from the existing uncommitted filesystem draft. Programme tracking is in
the [existing project](https://github.com/users/mattordev/projects/1).

## Existing capabilities and gaps

| Area | Implemented | Partial or missing | Reuse and entry points |
| --- | --- | --- | --- |
| MCP | Local stdio and remote Streamable HTTP discovery, validated schemas, explicit process trust, exact tool allowlists, lazy/persistent sessions, cancellation, timeout and cleanup | No need for another MCP manager; tool annotations alone are not authority | `ai/mcp/config.py`, `runtime.py`, `session.py`, `transport.py`, `remote.py` |
| Common execution | Native/external `Intent`, registry, dispatcher, structured arguments, `ToolResult`, clarification and one-use approvals | One selected invocation at a time; dependent tool orchestration and deferred-schema selection are absent | `ai/intents`, `ai/tools/arguments.py`, `validation.py`, `utils/on_command.py` |
| Filesystem reading | Directory listing with modification times; UTF-8 reads bounded to 1 MiB of actual bytes; canonical allowed roots, lexical/resolved blocked ancestors | Large files are rejected rather than partially read; no dedicated metadata, filename or content search | `integrations/coda-filesystem/server.py`; completed #174 |
| Filesystem context | Bounded relative read/list resolution within explicit workspace roots, ambiguity handling, no inferred home/cwd, cancellation and revalidation | No retained originating-directory search chooser, latest-file read sequence or new mutation resolution | `ai/mcp/workspace.py`; completed #170; open #232 |
| Filesystem writes | Existing **uncommitted** #43 draft creates files/directories with independent disabled-by-default write grants, mandatory host confirmation, exclusive file creation and Windows-name/link checks | Draft needs focused promotion and current remote CI; edit, append, copy, move, rename and delete are absent | Existing changes in server, manifest, example, README, host permissions/errors and tests |
| Web search | Opt-in SearXNG, bounded DDGS fallback, common result shape, URL attribution, timeouts and conversational errors | Search snippets are not fetched articles; page selection and retrieval absent | `integrations/coda-websearch`; completed #227/#229; discovery #231 |
| Browser | Generic configured third-party MCP support | No reviewed browser policy, optional browser bundle, isolated interaction or browser-specific resource lifecycle | Existing MCP/session lifecycle is the integration boundary |
| System information | Native system/program status, psutil memory and platform diagnostics | CPU/disk utilisation, optional GPU detail and authorised process observations need extension; #226 separately tracks conversational report selection and STT accuracy | `commands/status.py`; installed `psutil` |
| Execution | Runtime worker concurrency, active request cancellation and owned MCP subprocess lifecycle | User-command execution and managed background process tools are absent; an MCP launcher is not a command-execution policy | `runtime/active_request.py`, `runtime/runtime_queue.py`, `ai/intents/dispatcher.py` |
| Scheduling | Runtime events/queues and tool result presentation can be reused | No durable schedule store, recurrence engine, occurrence claims, schedule CRUD or run history found | Integrate with existing runtime; do not reuse transient conversation state as durable scheduling |
| Conversation and results | Bounded private result snapshots, deterministic summaries, stateless optional interpretation, sanitised cloud context and failure explanations | Retention does not execute a second tool; dependent actions require explicit dispatcher integration | `ai/tools/context.py`, `interpretation.py`, `presentation.py`, `utils/llm_service.py` |
| Tool selection | MCP discovery already populates common intents; deterministic matching and local classification exist | Classifier receives catalogue information without a dedicated searchable/deferred-schema composition surface | Extend current registry and workload controls; preserve v1.6.0 intent work |

The inventory is based on source and tests, including closed implementation
issues #139–#145, #169, #170 and #174, rather than README omissions. No feature is
considered missing merely because it lacks a separate command module.

## Existing work and integration workflow

The original checkout remains on `feature/web-search-mcp`. At the audit it is
seven commits ahead of its tracked feature ref because its tip is the already
published main promotion, not seven new filesystem commits. Fetching origin
confirms main `1317a16` and develop `f860969`.

Nine files are modified in that checkout. Eight contain the #43 creation draft;
`docs/release-notes-v1.5.5.md` has an independent wording correction. Preserve all
original bytes. Copy only the eight creation files into a separate focused
worktree for review; do not silently commit the unrelated release-note edit.
The filesystem draft and its historical validation are described in #43.

The established flow is **feature branch → develop → main**. Existing PRs use
`feat`, `fix`, `docs` and `release` titles and focused implementation commits.
At audit the only open PR is #131, an old `develop` → `main` provider-refactoring
PR, currently non-mergeable. Leave it unchanged; it is not authority to promote
this programme. No active filesystem feature PR was found.

Project conventions:

- Existing labels: `enhancement-system`, `enhancement-commands`, `documentation`
  and `bug`, with `epic:`, `plan:`, `feat:` and `docs:` title conventions.
- Native sub-issues are already used by epic #146. Existing issue types are null;
  do not invent a competing type scheme for this personal repository.
- Status options: Todo, Next, In Progress, Done, Issues and Scrapped. Use Todo
  for planned work, Next for the next ready item, In Progress through development
  and PR review, and Done only after accepted integration. Use Issues for a
  documented actual blocker, not every future dependency.
- There is no priority, iteration or release custom field. Use milestone
  assignment and native dependencies; Next identifies the immediate priority.
  Existing roadmap cards sometimes state priority in their prose.
- No new due dates. Preserve the separate v1.6.0 and v1.6.5 milestones, including
  their existing scope and any dates. Managed SearXNG discovery #230 stays separate
  from the required web capabilities and does not authorise deployment.

Release acceptance requires actual checks, practical integration evidence,
documentation, version/tag consistency and normal review/promotion. Do not close
issues merely because a PR exists, or publish a partial capability release.

## External implementations evaluated

References were inspected from current source and documentation. Before adoption,
pin and test a published package version: source-branch behaviour is not proof
that a particular package includes it. No external code was copied in this audit.

| Candidate | Maintenance, licence and footprint | Boundary fit and decision |
| --- | --- | --- |
| [MCP filesystem](https://github.com/modelcontextprotocol/servers/tree/c799c8552b0f27b7a33a7b9233773da144dddcf5/src/filesystem) | Active reference-server repository; current docs require Node 24+. Broader file tools, read-only modes and canonical root validation. Repository licence is undergoing MIT/Apache-2.0 transition; documentation has separate terms | Retain CODA's Python bundle. Replacement would add Node and migrate existing policy/configuration without removing the need for independent destructive grants, action approval or Windows validation. Use capabilities and security tests as references; revisit only for a demonstrated gap |
| [MCP Fetch](https://github.com/modelcontextprotocol/servers/tree/c799c8552b0f27b7a33a7b9233773da144dddcf5/src/fetch) | Active Python reference implementation, MCP SDK 2.x, optional extraction-related Node execution; source has bounded responses/decompression, redirect guards, destination restrictions and direct-connection DNS pinning. Inspected package metadata is `2026.11.18`, requires Python ≥3.11.5, and declares MIT | Leading candidate for optional HTTP reading, subject to exact package/version and dependency review. Current local Python 3.11.2 cannot install this version; use an explicitly selected compatible environment/version or make a reviewed baseline decision. Inspect proxy, robots fetch, extraction subprocess, output shape, timeout/cancellation and licence. Never enable private IP access merely to make tests pass; avoid optional Node extraction for baseline ordinary reads |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp/tree/2337aff0213d28db41d3e3daf48cee19b3d0bd82) | Apache-2.0; repository updated 18 November; Node 20+ and browser binaries. Stdio interface, accessibility inspection and screenshots; substantially greater disk/memory cost than HTTP retrieval | Evaluate as an explicitly enabled integration. Require isolated disposable sessions, no existing profiles/credentials, constrained tool grants, action-bound approval and network/subresource controls. Generic trust/allowlisting alone does not classify form submission or arbitrary script execution safely |
| [Desktop Commander](https://github.com/wonderwhy-er/DesktopCommanderMCP) | MIT; repository updated 24 November; Node-based filesystem, terminal and process tooling | Do not add now. Broad shell/process authority still needs CODA-specific command policy, current-grant validation, owned handles and sanitised output. Evaluate narrow tools only after execution policy is settled; reusing maintained code is useful only if authority can be enforced |
| MCP time server | Maintained within the same reference-server repository; provides time/timezone tools | Time lookup is not persistent task scheduling. Do not present it as a scheduler or add it to solve durable execution |

The MCP repository's current
[licence](https://github.com/modelcontextprotocol/servers/blob/c799c8552b0f27b7a33a7b9233773da144dddcf5/LICENSE)
describes the transition; its filesystem README still describes MIT. Resolve
notices at the selected package version rather than treating that README as the
only legal evidence. CODA has no tracked root licence file at this baseline;
do not infer permission to relicense CODA or vendor third-party code. Compatible
runtime integration and any redistribution must retain applicable notices.

## OpenClaw implementation findings

Source reference: [OpenClaw `28d47b9`](https://github.com/openclaw/openclaw/tree/28d47b9be7380da46c0f5ca175afb68d740aeaef),
MIT. These are capability references, not a proposed architecture migration.

- [Tool search](https://github.com/openclaw/openclaw/blob/28d47b9be7380da46c0f5ca175afb68d740aeaef/docs/tools/tool-search.md)
  separates compact names/descriptions from selected schema retrieval and
  dispatch. Policy-approved catalogue construction precedes discovery. Local
  model exceptions demonstrate that deferred schemas need model-specific
  validation rather than a universal assumption about model reliability.
- [HTTP reading](https://github.com/openclaw/openclaw/blob/28d47b9be7380da46c0f5ca175afb68d740aeaef/src/agents/tools/web-fetch.ts)
  separates bounded extraction from browser interaction and carries source
  information. Its implementation reuses shared network/untrusted-data helpers;
  proxy/DNS behaviour is a security boundary, not just URL syntax validation.
- [Execution](https://github.com/openclaw/openclaw/blob/28d47b9be7380da46c0f5ca175afb68d740aeaef/docs/tools/exec.md)
  and [approval](https://github.com/openclaw/openclaw/blob/28d47b9be7380da46c0f5ca175afb68d740aeaef/docs/tools/exec-approvals.md)
  distinguish host policy, executable restrictions, approval and process output.
  CODA needs the same distinction, implemented through its current dispatcher
  and worker ownership rather than a gateway/agent transplant.
- [Cron execution grants](https://github.com/openclaw/openclaw/blob/28d47b9be7380da46c0f5ca175afb68d740aeaef/src/agents/bash-tools.exec-cron-grant.ts)
  bind authority to job/configuration and command/cwd/environment, recheck the
  live occurrence/policy and account consumption without replaying a committed
  use. CODA must never store a conversational `confirmed=True` as universal
  scheduled authority.
- [Cron lifecycle](https://github.com/openclaw/openclaw/blob/28d47b9be7380da46c0f5ca175afb68d740aeaef/docs/automation/cron-jobs/how-it-works.md)
  distinguishes durable occurrence admission and results from transient request
  state. Document missed schedules, restart recovery and timezone behaviour.
  Atomic occurrence claims prevent duplicate starts, but cannot guarantee exactly
  once external effects after a crash; unknown effects must not be blindly retried.
- [Browser isolation](https://github.com/openclaw/openclaw/blob/28d47b9be7380da46c0f5ca175afb68d740aeaef/docs/tools/browser/isolation.md)
  uses owned profile/process resources. CODA should default to fresh isolated
  sessions and avoid importing an existing authenticated browser.

## Permission and resource decisions

Extend current operator-owned configuration. Independent read, write and
destructive grants describe filesystem authority; workspace roots provide
context only. Host tool allowlists and per-action approvals remain additional
gates. Every source and destination must be authorised independently. A tool
result, retrieved page or local model cannot create a grant.

The #43 draft rejects linked/reparse ancestors and exclusively creates new files.
Existing read helpers check lexical/resolved policy, but path checks and later
opens are not an OS sandbox. A hostile local process can race policy checks or
replace ancestors. Do not describe that residual race as solved. Follow-on
mutation work must state its supported threat model and use available
platform-safe handles/isolation where necessary; document limitations and do
not promise adversarial isolation from portable path checks alone. Reject
workspace escape through pre-existing symlinks/junctions in all new traversal.

Read limits must apply to actual bytes consumed, with explicit partial-result
metadata. Searches need entry/depth/byte/match limits and cancellation; a small
response alone does not bound an enormous scan. Mutation tools need conflict
detection, no accidental overwrite and honest interrupted-operation outcomes.

HTTP retrieval needs public-address checks for every connection and redirect,
including DNS rebinding, auxiliary fetches, compression and proxy behaviour.
Browser isolation does not itself provide network isolation. External source
text stays untrusted and privacy-classified before any model exposure.

Command execution defaults to deny, structured argument vectors, no elevation,
bounded stdout/stderr and owned process trees. Background work and scheduling
must not block conversation workers. Persist schedules/run state locally with
atomic occurrence claims and bounded history; select the smallest mechanism
that meets demonstrated concurrency and recovery requirements.

Tool composition requires total step/time/output/context/retry limits, current
permissions and separate approvals for each action. A failed mutation with an
unknown outcome must not be retried automatically. Use current Intent and
ToolResult contracts, cancellation and provider privacy/workload paths.

## Validation baseline and platform limits

Current original-checkout focused validation:

```text
PYTHON_DOTENV_DISABLED=1
python -m unittest tests.test_filesystem_mcp tests.test_mcp_bundled \
    tests.test_mcp_workspace tests.test_conversational_mcp_failures -q
74 tests, OK (6 skipped), 31.733 seconds; Windows / Python 3.11.2
```

This includes actual temporary files, native Windows junction checks and real
MCP subprocess round trips. Privilege-dependent symbolic-link and optional
archive-environment tests are skipped where unavailable. It is not evidence for
Linux, live LLM tool selection, acoustic voice interaction or the newly planned
capabilities. The previous #43 full-suite count is historical evidence only.

The configured llama.cpp-compatible server responds to its model catalogue
request (seven advertised entries); that establishes service reachability only,
not successful generation or tool composition. The configured Ollama endpoint
does not respond during this audit. No model was downloaded or loaded merely
to inspect service availability.

CI already targets Windows/Ubuntu and Python 3.11/3.12. New work must obtain
current-head matrix evidence. POSIX symlinks, Windows junctions/reparse points,
cross-device moves, process-tree termination and filesystem case rules require
native platform validation. Browsers add separate binaries, Linux libraries and
RAM; keep them optional and provide measured requirements after evaluation.
GPU information may be unavailable without vendor interfaces; do not require
drivers or install them automatically. Basic filesystem/HTTP/status tools must
remain useful on CPU-only modest hardware without loading another model.

## Release boundaries

Deliver v1.5.6 filesystem capabilities, v1.5.7 web capabilities, v1.5.8 execution
and schedules, then v1.5.9 composition. Independent discovery can proceed while
earlier work is reviewed, but release gates retain the dependency order.

Persistent searchable conversation memory, agent delegation, external messaging,
the v1.6.0 semantic classifier, the v1.6.5 WebUI and full-duplex voice redesign
remain outside this programme. Preserve their issues and milestones.
