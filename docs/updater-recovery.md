# Updates and recovery

This documents the updater planned for v1.4.4: release selection (#130), source
activation/rollback (#128), and staged dependencies/restart (#129). It does not
mean that v1.4.4 has been published or that its Windows/Linux release validation
has passed. Keep the manual-update guidance in published release notes until
those checks are complete. Close all other CODA instances before attempting an
update; this is a startup updater, not a live hot-update system.

## First upgrade from an older installation

**Do not use the built-in updater in v1.4.2 or earlier to obtain this repair.**
Decline its update prompt and close CODA. That older code cannot retroactively
gain the new dependency, Git-checkout, or rollback protections simply because
a repaired version exists upstream.

When v1.4.4 is published:

1. Back up the old installation, including `.env`, `wakewords.json`,
   `commands.json`, and any local customisations. Do not publish these backups;
   they may contain credentials.
2. Download the **v1.4.4 release's source archive** from
   [GitHub Releases](https://github.com/mattordev/coda/releases), not the latest
   main-branch archive. Extract it into a new directory beside the old install.
   Keep the original directory intact for recovery.
3. Copy your configuration files into the new directory. Merge any missing
   settings from `docs/.env.example` without replacing existing credentials or
   intentional overrides. Review custom code separately; copying old source
   over the new release can restore the unsafe updater.
4. In the **new directory**, create a fresh Python 3.11 environment and install
   the release's requirements. Do not copy or relocate the old `.venv`.

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Environment creation failed" }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
& .\.venv\Scripts\python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency verification failed" }
& .\.venv\Scripts\python.exe main.py -m
```

Linux shell:

```sh
python3.11 -m venv .venv &&
.venv/bin/python -m pip install -r requirements.txt &&
.venv/bin/python -m pip check &&
.venv/bin/python main.py -m
```

Follow the platform prerequisites in the README, including PortAudio where
needed. Pocket TTS and ElevenLabs playback require FFmpeg's `ffplay` on PATH.
Existing model caches can be reused if your cache settings still identify the
same location; a fresh Python environment does not include those model assets.
Check manual-mode commands, configuration, and the providers you use before
retiring the old installation. If setup fails, close the new instance and use
the unchanged old installation while investigating.

Git-based development installations should save their work and update through
Git, then install the chosen version's requirements. The repaired automatic
updater deliberately refuses Git checkouts; do not remove `.git` to bypass it.

## Release selection

- Discovery uses this repository's latest published stable GitHub release,
  not `main/version.json` or `main.zip`. A draft, prerelease, missing release,
  invalid semantic version, or missing local `version.json` stops automatic
  updating without creating replacement version metadata.
- The local version is read from the installation directory, independently of
  the terminal's working directory. Equal or older release versions are not
  installed. The release tag and staged `version.json` must identify the same
  version; release CI checks that tagged versions match the tracked metadata.
- The tag is resolved to a full commit SHA, following at most five annotated
  tags. The archive is fetched from the fixed GitHub codeload URL for that SHA;
  `target_commitish` and mutable branch archives are not used. Confirmation and
  preparation use the same selected commit, with another version check before
  preparation. The extracted root must be `coda-<40-character commit SHA>`.
- Metadata requests use 5-second connect and 15-second read/inactivity timeouts,
  a 1 MiB response limit, and a 45-second discovery budget. Archive requests use
  the same connection/inactivity timeouts and a 180-second transfer budget.
  Budgets are checked between chunks/calls; they are not a hard wall-clock
  cancellation guarantee for an in-progress socket operation.
- Archives are limited to 128 MiB compressed, 512 MiB expanded, and 10,000
  entries. Source metadata and archive layout are checked before dependency
  installation. Offline, rate-limited, malformed, missing, or over-limit
  responses leave the running installation unchanged; there is no fallback to
  mutable main-branch source.

Only installations already running the repaired updater can use this flow for
later releases. Publishing v1.4.4 does not make the older updater safe to use.

## What source activation protects

- Git checkouts, worktrees, and installations nested inside a checkout are
  refused before any download or staging. Update those installations using Git;
  a version mismatch must never replace development work with a source archive.
- The installation is located from the updater module, not the terminal's
  current working directory.
- Each attempt downloads and stages into a unique `.coda-update-*` directory
  inside the installation. Older backups are never used for automatic rollback.
- Archive traversal and symlinks are rejected. Activation also rejects links
  and Windows junctions in paths it would replace.
- Virtual environments, `.git`, and reserved local directories are not replaced.
- Existing `.env`, `wakewords.json`, and `commands.json` override downloaded
  defaults. Unrelated top-level files remain in place.
- Replaced files and directories are moved into that attempt's
  `coda-old-version` directory. Custom edits inside replaced source directories
  remain in this backup; they are not automatically merged into new code.
- A caught activation error reverses only moves made during that attempt.
  Preparation/activation failures are reported explicitly, including incomplete
  rollback. The old runtime never resumes using a mixture of old and new code.

## Dependencies and restart

Interactive terminals show a Colorama-coloured ASCII activity bar and elapsed
seconds during download, extraction, environment creation, dependency installation,
and verification. This is activity feedback, not a percentage or a guarantee that
network traffic is flowing. Redirected output uses plain stage messages instead.
Detailed dependency output remains in the attempt's private `dependency-install.log`.

1. Before changing live source, create a fresh environment at the permanent path
   `.coda-update-<attempt>/environment` using the current Python version.
   The existing `.venv` is not modified, moved, or deleted.
2. Install the downloaded release's pinned `requirements.txt` with that new
   environment's Python, then run `pip check`. Pip runs in isolated mode without
   user/global target overrides. Creation, installation, and verification have
   separate timeouts (2 minutes, 20 minutes, and 2 minutes). Failures leave the
   live source/environment untouched and retain a local `dependency-install.log`.
3. Return from startup before starting any old-runtime workers, run runtime
   cleanup, recheck the Git guard, and only then activate source.
4. Start the new interpreter with the original CLI arguments and installation
   working directory. The child loads configuration and commands, then signals
   readiness and waits without starting workers or reading terminal input.
5. Commit `.coda-update-active.json` and let the child proceed. Later launches
   through `main.py` consult this selection **before importing the runtime**,
   even if launched with the original `.venv` interpreter. The supervising parent
   waits for the child so the shell does not compete for terminal input.

Do not relocate or delete the selected managed environment: Python virtual
environments embed their installation paths. These are per-update environments,
not a separate Pocket TTS environment. The active-selection file is Git-ignored.

If spawn, imports, configuration setup, or the 45-second readiness check fails,
the child is stopped before restoring source and the previous environment
selection. If the child cannot be stopped, source is left in place and manual
recovery is required. Readiness is not a microphone, model, or live audio test;
failures after handoff are normal runtime failures and do not trigger rollback.

Model assets are not downloaded as part of dependency installation. Existing
Hugging Face/model-cache settings are inherited and the updater never clears
them. Missing `ffplay` produces guidance to install FFmpeg and put `ffplay` on
PATH; it does not block a pyttsx3-only installation.

## Recovery and limitations

The updater prints the attempt directory on failure and the backup directory
on success. No recovery files are automatically deleted. These directories are
Git-ignored and may contain private configuration: do not upload them publicly.

If rollback also fails, or the process is forcibly terminated during activation,
stop CODA and inspect the live installation, `coda-version-new`, and
`coda-old-version` inside the printed attempt directory before restoring files.
Do not blindly restore an older attempt. Keep a copy of all recovery files until
the installation has been checked. There is no crash-resume journal or atomic
whole-installation switch yet.

For manual recovery, restore the source and environment selection as a matched
pair. When a previous selection exists it is saved as `previous-environment.json`
in the attempt directory. With CODA stopped, retain a copy of
`.coda-update-active.json` before restoring that saved selection. If reverting
to the original source/environment (no previous selection), remove that
selection file and use the original interpreter; do not leave the new selection
pointing at incompatible dependencies. No old environment is automatically
deleted. Do not share dependency logs publicly without checking for credentials.

The updater does not remove files deleted upstream. Releases without the new
restart bootstrap are refused before activation. A commit-addressed archive
pins the selected source; it is not an independently signed release or a test
of provider hardware, audio quality, or downloaded model assets.

## Validation

Unit tests inject dependency, activation, and startup failures. Offline process
tests create disposable Python environments and exercise handoff, rollback, and
later-launch environment selection without downloading models or running CODA
against hardware. Full-flow fixtures also exercise mocked release discovery and
archive transfer, install a small real wheel offline, and verify that the new
dependency is available to the restarted process. These are fixture upgrades,
not validation of a published v1.4.4 archive.

The configured CI matrix runs the tests on Windows and Ubuntu
with Python 3.11/3.12; a configured job is not evidence that this branch has
passed it. Remote matrix results and the actual release remain release gates.
Set `CODA_RUN_UPDATE_ENV_SMOKE=1` to additionally create a real environment with
pip and verify an offline requirements install plus `pip check`.
