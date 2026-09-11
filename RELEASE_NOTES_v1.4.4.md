# C.O.D.A v1.4.4 — Reliable Updates and Recovery

C.O.D.A v1.4.4 replaces the old source-overwrite updater with a staged,
version-aware process that prepares dependencies before activation and can roll
back a failed restart.

## Highlights

- Discovers the latest published stable GitHub release and resolves its tag to
  an immutable commit before downloading source.
- Rejects mutable branches, prereleases, mismatched versions, malformed
  metadata, oversized responses, unsafe archives, links, and path traversal.
- Caches validated release discovery for one hour while allowing an explicit
  refresh before accepting a cached update.
- Refuses automatic source replacement inside Git checkouts, including nested
  installations and worktrees.
- Preserves `.env`, `wakewords.json`, `commands.json`, local environments, model
  caches, and unrelated local files.
- Downloads and validates source in a unique recovery directory without
  changing the running installation.
- Creates a fresh permanent virtual environment from the release's pinned
  requirements, runs `pip check`, and keeps detailed package output in a private
  recovery log.
- Shuts down the old runtime before activation, supervises the replacement
  process, and commits future interpreter selection only after startup is ready.
- Stops a failed replacement before restoring the previous source and
  environment selection.
- Adds terminal progress, elapsed time, estimates, transfer percentage, speed,
  and remaining-time feedback without exposing dependency logs.
- Adds a disposable developer sandbox for exact-commit update, restart, and
  later-launch validation.

## Important first upgrade

**Do not use the built-in updater in v1.4.2 or earlier to install v1.4.4.** The
older updater cannot gain these protections retroactively.

Decline the old update prompt, close CODA, back up the installation and private
configuration, then download the v1.4.4 source archive from GitHub Releases into
a new directory. Copy or merge configuration without replacing credentials,
create a fresh Python 3.11 virtual environment, install `requirements.txt`, run
`pip check`, and start CODA from the new environment.

See [Updates and recovery](https://github.com/mattordev/coda/blob/v1.4.4/docs/updater-recovery.md)
for the complete Windows and Linux procedure, recovery steps, and safety
constraints. Git-based development installations should continue to update
through Git.

Once an installation is running v1.4.4, the repaired updater can safely prepare
later published releases at startup.

## Validation

- Full Windows Python 3.11 suite passed with 492 tests and two expected skips.
- The opt-in real-environment smoke test passed in a separate full-suite run.
- A disposable non-Git installation completed exact-commit download,
  validation, fresh dependency setup, activation, supervised restart, and
  later-launch environment selection.
- The final CI matrix passed on Windows and Ubuntu with Python 3.11 and 3.12.
- Release tag and tracked `version.json` validation passed for `v1.4.4`.

CI and the sandbox do not validate microphone/audio hardware, provider accounts,
model downloads, or every user machine.

## Known limitations

- Recovery attempts and old environments are retained for manual inspection;
  they are not automatically deleted.
- There is no crash-resume journal or atomic whole-installation switch yet.
- Files deleted upstream are not removed automatically.
- Commit-addressed source prevents branch drift but is not an independently
  signed release artifact.
