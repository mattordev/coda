# System execution and scheduling contract

This defines v1.5.8 work for #254–#261. Implementation and platform evidence
remain required before closing those cards or releasing v1.5.8.

## Authority

Use operator-owned named actions with exact absolute executable, fixed argv,
fixed working directory and explicit environment-variable references. A request
contains an action ID, never shell text, executable paths, environment overrides
or additional arguments. No PATH lookup, shell expansion, elevation or implicit
grant from a model/tool response is supported. Enabling the integration alone
grants nothing. Policy paths and scheduled authority are protected configuration.

An operator can deliberately approve an interpreter with fixed script arguments;
the executable and scripts must be reviewed as code with that user's privileges.
This is an execution policy, not an OS sandbox for hostile programs. Filesystem
and network restrictions on other tools do not constrain a separately approved
program. Changing a script at the same path remains a meaningful risk; executable
and script identity binding must be resolved in implementation before acceptance.

Foreground execution and background launch require one-use host confirmation
bound to exact action/configuration. Scheduled authority is a separate explicit
operator grant plus confirmation of the exact task definition. Ordinary approval
cannot silently become recurring authority. Modification or changed configuration
invalidates the saved binding; current policy is checked again at the actual run.

## Native integration decision

Use CODA's existing dispatcher, cancellation and MCP lifecycle, backed by a small
native subprocess service. A broad arbitrary-shell MCP interface would require a
second policy surface to constrain paths/argv, bind durable grants and manage
owned background processes. No third-party shell server is installed or exposed
as a shortcut. Python's [subprocess interface](https://docs.python.org/3.11/library/subprocess.html)
supports explicit argv, cwd, environment and return codes; bounded streaming
and process-tree ownership remain CODA's responsibility.

The reviewed OpenClaw execution/cron implementation separates standing grants,
job configuration revision, exact operation binding and current host-policy
checks. Apply those boundary principles without copying its architecture or
granting its broader shell/session authority. The earlier pinned source review
is retained in the programme audit; current [execution](https://docs.openclaw.ai/tools/exec)
and [automation](https://docs.openclaw.ai/automation/cron-jobs) documentation provide
capability references, not acceptance evidence for CODA.

## Process lifecycle

Processes run without stdin interaction and without ambient secret environment
inheritance. Drain stdout/stderr continuously into bounded buffers, recording
exit status and explicit truncation. Redact configured secret values before
results enter logs, history, speech or model context. No automatic execution retry.

Windows needs an owned kill-on-close Job Object established before the approved
program starts, with hidden subprocess windows. A trusted waiting supervisor can
be assigned before receiving its launch gate. Linux uses a new owned process
session/group. Child cleanup and permission revocation need real descendant tests
on both platforms. Privileged/hostile escape is outside these process controls;
never claim an OS-level sandbox. Shutdown/restart cancels owned background work;
handles are opaque, scoped to the service and cannot address unrelated PIDs.

## Durable schedules

To enable the scheduler service, set `CODA_SYSTEM_POLICY` to the absolute path
of an operator-owned JSON policy. Keep it in a private directory outside the
installation so the policy and its adjacent `.schedules.sqlite` database survive
updates. The service starts with CODA and stops when CODA closes.

Use the standard-library [SQLite interface](https://docs.python.org/3.11/library/sqlite3.html)
for bounded task/run/attention records, schema migration and atomic occurrence
claims. Keep durable task storage separate from browser/MCP observations and
conversation history. Claim once in a transaction before launching. A crash can
leave an unknown external effect; mark interrupted work and do not replay it.

Support explicit one-time timestamps and bounded recurring intervals initially.
Store UTC instants with the user's named timezone. Ambiguous/nonexistent local
times require clarification. Interval recurrence is elapsed-time recurrence,
not an implicit daily wall-clock promise. Missed occurrences are skipped and
recorded; overlapping work does not spawn another copy. Pause/cancel is durable.
Task edits invalidate pending occurrences and saved approval bindings.

The scheduler is an owned non-blocking service, not a conversation worker that
sleeps until a due time. Bound active jobs, stored tasks, retained output/history
and notifications. Persist completion/failure attention before delivery; delivery
retries must not execute an action again. Text/headless inspection works without
audio or a WebUI. No external messaging integration is introduced.

## Release workflow

All v1.5.8 work stays on `feature/system-and-scheduler`, followed by a PR into
develop and a separate develop-to-main release PR after acceptance. Include the
already integrated #306 voice/web fixes in that release. Retain private grants;
default examples remain disabled. Required Windows/Linux tests, native process
and durable restart checks, setup documentation and published-source verification
remain release gates. v1.5.9 composition and v1.6.0 local embedding matching follow
as separate releases, through and including v1.6.0.
