# Filesystem capability validation

This is the v1.5.6 release gate for [#267](https://github.com/mattordev/coda/issues/267),
updated 26 November 2026. Development integration is not a published release.
Main and the latest published release remain v1.5.5 until the documentation and
release cards pass their own gates.

## Traceability

| Capability | Accepted issue | Integrated PR |
| --- | --- | --- |
| Policy and workspace audit | #239 | #279 |
| Exclusive file/folder creation | #43 | #280 |
| Bounded partial UTF-8 reads | #240 | #281 |
| Metadata | #241 | #283 |
| Filename search | #242 | #284 |
| Conversational listings and bounded follow-ups | #232 | #285 |
| Literal content search | #243 | #286 |
| Conflict-checked text editing and append | #244 | #287 |
| Bounded file/tree copying | #245 | #288 |
| Independently approved deletion | #247 | #289 |
| Move and rename | #246 | #290 |

## Automated evidence

The local complete suite at move head
`0fe12eed1ab462d48fe24608aab044d232df8f57` passed **1,207 tests with 19 skips**.
All ten checks passed at that head: Windows and Linux with Python 3.11 and 3.12
for both push and PR regression workflows, plus both Pylint jobs. PR #290 is
integrated into develop at `d6fb94b`. Earlier capability PRs also passed their
intended integration checks; linked PR descriptions record their exact evidence.

Focused filesystem suites exercise disposable actual files, binary/text bytes,
UTF-8/CRLF boundaries, partiality, source conflicts, current read/write/destructive
policy, blocked descendants, no-overwrite publication, root/workspace protection,
resource limits, interrupted effects and recovery evidence. Host confirmation
tests prove that no action happens before approval, cancellation preserves data,
the retained request cannot expand, and approval is consumed once.

Real SDK tests separately cover bundled discovery, allowlists, operation and
persistent sessions, current permissions at invocation and owned child cleanup.
Cancellation/timeout fixtures pause actual content reads, the first copied
chunk, a completed unlink, or a completed rename; they do not infer cancellation
merely from a mocked callback. Uncertain mutation outcomes remain failures and
are not automatically replayed. The source and remaining effects are inspected.

The operator-protection extension adds host rejection and real direct-SDK
rejection of mutations to definition directories and external bundle authority
files, including disabled/invalid configurations and future definitions. A
direct request bypasses host permission checking to prove the child enforces
the same exclusions. Tests also cover hard-link aliases, protected ancestors,
invalid internal metadata, bounded launch data, case-insensitive recovery names,
normal data mutations, authorised reads and persistent-session exclusion changes.
Its final head `cbbb8d62ab035ae64c2b713dc6ef6d253d28a43d` passed **1,221 local
tests with 19 skips** and all ten Windows/Linux Python 3.11/3.12 regression and
Pylint checks. This includes fourteen new protection/approval cases and creation
prompts naming the target for renamed bundled filesystem instances. The earlier
1,220-test local run also completed successfully; no process was stopped. The
security gate was accepted in PR #291.

## Native platform evidence and limits

Windows tests use native junctions and compare private file DACLs across text
replacement. PowerShell child environments are isolated from an incompatible
PowerShell 7 module path when invoking Windows PowerShell 5. Linux CI exercises
native inherited/default ACL handling and a real cross-filesystem move fixture
when `/dev/shm` is writable and on a distinct device. A reported skip is not a
successful native check. Local Windows symlink creation is unavailable without
the required OS capability; junction and hard-link checks run separately.

CPython's Windows path/handle `ctime` mismatch was reproduced by CI. The fix
compares compatible birth timestamps across APIs while retaining full before/
after handle change timestamps. Portable regression tests prove both the
harmless mismatch and rejection when the handle changes with unchanged size and
mtime. See [CPython #157671](https://github.com/python/cpython/issues/157671).

No privileged bind-mount test is claimed. Protection compares existing physical
identities as well as paths, but this is a cooperative local filesystem policy,
not an OS sandbox against an adversarial process with the same user privileges.
Blocking system calls remain subject to host transport timeouts; no claim of
hard real-time filesystem cancellation is made.

## Live local-model evidence

Disposable end-to-end runs used the configured local llama.cpp provider with
cloud providers disabled. Each proposed mutation's exact arguments were checked
before confirmation. No test operated on real user documents.

- Listing, retained Markdown filtering and newest-report reading used originating
  directory context. A cold model timeout caused no tool action; a warm retry
  completed the scenario.
- Content search selected the bounded content tool, returned the correct matching
  line, respected a blocked file, and handled the contextual follow-up.
- Append/edit proposed the exact path, text and expected revision, left bytes
  unchanged before approval, and produced the expected bytes after confirmation.
- Copy preserved the source and reproduced text/binary descendants at the exact
  new destination, without effects before approval or leftover staging.
- Delete used separate destructive authority with no read/write grants. Cancel
  preserved the tree; a fresh confirmed request deleted only that tree.
- Move used destructive-source and write-destination authority with no read
  grants. Cancel preserved the tree; a fresh confirmation moved text/binary
  descendants to the exact destination and cleaned relocated locks.
- A combined disposable workflow on the operator-protection implementation
  created a folder/report, used a real local-model listing and contextual newest
  report read, obtained revisions, appended/edited, copied, searched names/text,
  checked metadata, moved and deleted. Exact effects and lock/server cleanup
  passed, and a policy self-modification attempt was rejected before approval.
  Its first listing attempt timed out at 60 seconds without running that request;
  the retry used a 120-second local provider limit and completed. These were
  sequential confirmed requests, not autonomous composition.

Owned-server and fixture cleanup passed for the successful runs. A cold deletion
attempt also timed out before selection with no effects; the warm retry passed.
These are actual local-model runs, separate from deterministic fixture tests.
Multi-step autonomous composition remains a later v1.5.9 release, not an implied
capability of the bounded filesystem follow-up grammar.

## Operator configuration and migration boundary

Private operator configuration was preserved. No destructive grants were enabled
automatically, and no running manual CODA process was restarted or killed. New
tools require explicit allowlist entries and the appropriate separate grants.
Existing omitted destructive configuration remains disabled. Detailed setup,
migration and safe manual commands belong to #268 and the integration README.
