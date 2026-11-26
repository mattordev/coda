# v1.5.6 - Filesystem Capabilities

This release expands the opt-in filesystem integration from listings/reads into
bounded everyday file management. Folder results, searches and confirmed actions
have conversational replies in manual and voice modes, with detailed output
available on request. Existing operator grants remain unchanged.

## Changes

- Read bounded UTF-8 ranges with explicit partial scope and continuation offsets;
  obtain file metadata without reading contents.
- Search filenames within bounded trees and search literal text with line/column
  locations, bounded excerpts, byte/entry/time limits and incomplete-scan notices.
- Create files/folders exclusively. Edit one exact text occurrence or append text
  using a complete read's SHA-256 to detect intervening changes. Preserve exact
  bytes/newlines and file security across supported replacement operations.
- Copy ordinary files and directory trees with separate source/destination
  authority, bounded staging and no overwrite. Move/rename within one filesystem
  using destructive-source and write-destination authority; cross-filesystem moves
  fail without a copy/delete fallback.
- Delete files or explicitly recursive directory scopes only under separately
  enabled destructive authority. Protect roots/workspaces, blocked descendants,
  linked/reparse entries and transaction evidence.
- Require a fresh one-use confirmation for every bundled filesystem mutation,
  naming the target or both source/destination paths. Approval rechecks current
  policy and cannot expand or replay a request.
- Support bounded contextual filenames/recent-file filtering, selected report
  reads and partial-read continuation without repeating a directory path.
  Fresh searches/reads still pass normal tool and filesystem permission checks;
  missing, stale or ambiguous context asks for clarification.
- Protect the running installation, MCP definition directories and declared bundle
  configuration files, including external/disabled instances, from filesystem
  mutations. Host and standalone child enforce the same bounded exclusions.
  Persistent sessions cannot reuse stale operator-protection launch metadata.

The integration uses the existing MCP SDK and standard library. No new filesystem
service, Node installation, browser installation or third-party server is needed.

## Configuration and migration

Read, write and destructive grants are independent. Both the filesystem permission
file and the exact tool allowlist must authorise an action. Installing an update
does not add tools, roots, trust or destructive permission. An omitted destructive
section remains disabled, and the shipped examples keep writes/destructive access
disabled. Workspace roots provide read/list context and grant no access.

Retain private `.env`, MCP definitions and `*.config.json` files. Legacy explicit
launch definitions continue to work; opting into the bundled identity preserves
the existing ID, trust, grants and session choices. Carry custom `--config` paths
into `settings.config_file`. Restart after definition changes so the current
schemas/allowlists load. Operator-protection changes also require retiring the
affected persistent session or restarting CODA.

Follow the [safe test route](filesystem-quickstart.md) for a separate temporary
`filesystem_smoke` instance, read-only checks, write opt-in and optional destructive
checks without replacing an existing private definition. See the
[integration contract](../integrations/coda-filesystem/README.md) for limits and
[conversation guide](mcp-conversation.md) for retained versus fresh observations.

## Validation and limits

The final filesystem/security implementation passed **1,221 local tests with
19 platform/environment skips**, plus Windows/Linux Python 3.11/3.12 CI. Real SDK
tests cover operation/persistent sessions, permissions, confirmation, cancellation,
timeout, interrupted effects and owned-process cleanup. Native Windows junction
and private-DACL tests and Linux default-ACL/cross-device tests run separately;
unavailable local Windows symlink creation is disclosed rather than counted as a
successful check. See the [validation report](capability-filesystem-validation.md)
for tested heads and evidence.

Live local-model runs used disposable data with cloud providers disabled. The
combined workflow covered creation, listing, contextual reading, revision checks,
append/edit, copy, both searches, metadata, move, deletion and protected-policy
denial with verified effects and cleanup. A cold 60-second listing timeout ran
no action for that request; a 120-second retry completed. Migration was tested
through the real SDK with unchanged permission bytes and no automatic new grants.

Mutations and tree operations refuse linked/aliased/blocked or unsupported entries.
Text editing/revision reads are limited to complete UTF-8 files up to 1 MiB.
Default tree limits are 1,024 entries/eight levels for mutations; individual tools
document their maxima and search limits. This is cooperative application policy,
not an OS sandbox against an adversarial process with the same user privileges.

Cancellation, timeout or a lost rename response can occur after effects. CODA
reports uncertainty and retains bounded lock/staging/backup evidence rather than
automatically replaying or recursively cleaning uncertain data. Inspect both
locations and recovery output before a fresh request. Deletion is permanent and
does not use a recycle bin or promise undo. Cross-filesystem move and unsupported
safe-publication semantics fail clearly.

This release does not add web-page retrieval, browser interaction, command
execution, persistent scheduling or autonomous multi-tool composition. Those
capability releases remain tracked separately in the
[programme](capability-expansion-programme.md).

Delivery is tracked in [the filesystem epic](https://github.com/mattordev/coda/issues/235)
and [release gate #269](https://github.com/mattordev/coda/issues/269).
