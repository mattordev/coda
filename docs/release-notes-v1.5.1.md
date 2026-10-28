# C.O.D.A v1.5.1 — Bundled First-Party MCP Servers

CODA can ship optional first-party MCP servers that launch with its active Python
environment after explicit enablement. This release also improves tool-result
feedback, retained-result follow-ups and workspace references.

## Changes

- Manifest-driven bundled catalog with strict settings and independent availability,
  enablement, trust and exact tool permissions. Discovery grants no tool authority.
- Portable read-only filesystem server: directory listings and bounded UTF-8 file
  reads. Configure explicit allowed roots and blocked paths; add `read_file` to
  the tool allowlist only when wanted. Reads reject files larger than 1 MiB.
- Source archive updates and rollback preserve private MCP definitions and local
  server configuration, and reject activation after intervening permission changes.
- Accurate spoken failure explanations, sanitized visual diagnostics and bounded
  question-aware directory summaries, with full tool results retained separately.
- Privacy-aware conversational interpretation of long tool results. Simple results
  and errors stay deterministic. Narrow follow-ups use the retained result without
  rereading files or replaying tools; explicit raw-output commands remain available.
- Optional `read.workspace_roots` resolves relative references for bundled read
  tools. Workspace context grants no filesystem permission; ambiguous matches ask
  for a new, concrete request instead of choosing a file or searching recursively.
- Optional background model prewarming, disabled by default, with cancellation,
  memory guards and independent failure handling.
- Patch certifi, filelock and setuptools dependency advisories. No unrelated model
  or server configuration change is required.
- Document AI assistance and the project's owner-led learning practices.

## Setup and compatibility

Existing bundled definitions must explicitly set `enabled: true`. Keep `trusted`,
`allowed_tools`, confirmation and root permissions narrow. New tools receive no
automatic grant. Legacy explicit process definitions and remote MCP definitions
retain their existing behavior. Private files are not migrated automatically.

See [bundled setup](mcp-bundled.md), [filesystem configuration](../integrations/coda-filesystem/README.md),
[conversational results](mcp-conversation.md), [workspaces](mcp-workspaces.md) and
[prewarming](model-prewarming.md). The source updater remains disabled in Git
checkouts; use Git there. Older archive users should follow [update recovery](updater-recovery.md).

## Validation and limitations

Release preparation exercises the supported Windows/Ubuntu Python 3.11/3.12 matrix,
errors-only Pylint, disposable local/remote MCP servers, relocated source archives,
configuration preservation, workspace denials and bounded file reads. Exact CI,
merge and publication evidence is maintained in [release gate #171](https://github.com/mattordev/coda/issues/171).
These notes do not themselves claim a tag, CI pass or publication.

The dependency audit found no known advisories in 94 applicable pinned packages
after patching. The temporary audit input normalizes Torch's `2.13.0+cpu` to
upstream `2.13.0`; it does not attest the CPU wheel's build provenance.

Tool-result interpretation still depends on model speed and configured provider
deadlines, and reports partial-source use. Per-workload thinking/token budgets
are planned for v1.5.2; incremental voice output is planned for v1.5.3. Neither is
included here. Model prewarming does not eliminate inference latency.

Filesystem policy is an application check, not an OS sandbox: concurrent link
replacement remains a filesystem race. Avoid adversary-controlled allowed roots.
Workspace matching is bounded and rejects links/junctions. Tests do not validate
every third-party integration, model, microphone or audio device.
