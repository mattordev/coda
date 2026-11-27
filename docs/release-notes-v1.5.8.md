# CODA v1.5.8 — System Execution and Scheduled Tasks

CODA can run named local actions defined by the operator, either immediately or as background jobs. Confirm each launch and use a job ID to inspect or cancel background work.

- Create one-time or recurring schedules. CODA verifies the current grant before dispatch and keeps outcomes available across restarts. If a run was interrupted, CODA records it without replaying it.
- System status includes CPU, disk, and CODA process memory. CUDA allocation is available when CODA already has CUDA initialized.
- Spoken web follow-ups are more reliable. Source comparisons report failed reads, and saved browser captures can be located from the conversation.

Local actions and scheduling are off by default. See the [setup and smoke test guide](docs/system-execution-policy.md). Schedule creation and editing use structured typed commands.
