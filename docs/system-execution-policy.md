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

For a smoke test, save a private JSON policy with these fields. Replace both
paths with an absolute Python executable and an existing disposable directory.

```json
{
  "enabled": true,
  "actions": {
    "python-version": {
      "executable": "C:/Python311/python.exe",
      "argv": ["--version"],
      "cwd": "C:/Temp/coda-system-test",
      "background": true,
      "scheduled": true,
      "timeout_seconds": 10,
      "max_output_bytes": 4096
    }
  }
}
```

Start CODA in manual mode with `CODA_SYSTEM_POLICY` set in the launching
shell. Try these commands, prefixed by your usual wake word.

- `run action python-version`, then `confirm`. Expect Python's version and exit code 0.
- `run action python-version in background`, then `confirm`. Use
  `action status <job-id>` to check the result. A running job can be stopped
  with `action cancel <job-id>`.
- `schedule create {"action":"python-version","local_time":"2027-01-15T09:00","timezone":"Europe/London"}`,
  using a time a minute or two in the future. Confirm the UTC time in the
  preview. Use `schedule list` to check the saved task and `schedule history`
  after it runs.
- Use `schedule pause <task-id>`, `schedule resume <task-id>`, or
  `schedule cancel <task-id>` to control future runs. To edit, use
  `schedule edit` with the same JSON fields plus `task_id` and confirm again.
  Optional `interval` specifies recurrence in seconds.
- Use `schedule pending` to see unacknowledged results and
  `schedule acknowledge <task-id> <due>` to mark one as seen. The `due` value
  shown in the result is a UTC epoch timestamp in seconds.

For offline inspection, run
`python -m runtime.schedule_inspect <database> <tasks|history|pending>`.
This opens the existing database read-only.

To turn off automatic execution, remove `CODA_SYSTEM_POLICY` from the
launch environment and restart CODA. To retain history when switching
installations, keep the policy and database together in the same external
directory. Before copying either file, close CODA and back up both files.
Startup migrates supported older database schemas in a transaction.
An incompatible or corrupt database stops scheduler startup while normal
conversation remains available.

The scheduler runs while CODA is open. After a restart it records interrupted
occurrences without replaying them. It skips elapsed intervals outside the
30-second grace window. Revoking an action grant before dispatch prevents the
run. After a recurring grant is denied, review and edit the paused task before
resuming it. Schedule history retains the 512 most recent terminal outcomes.
Background jobs belong to the current session and are cancelled on shutdown.

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
