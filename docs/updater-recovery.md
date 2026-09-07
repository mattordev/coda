# Updater recovery work

Source activation/rollback (#128) and staged dependencies/restart (#129) are
implemented. Continue updating manually until release selection and end-to-end
upgrade validation (#130) are complete. Close all other CODA instances before
attempting an update; this is a startup updater, not a live hot-update system.

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

This step still downloads the main-branch source archive and does not remove
files deleted upstream. Releases without the new restart bootstrap are refused
before activation. Versioned release selection and an upgrade path from older
updaters remain #130, not proof supplied by these staging/restart tests.

## Validation

Unit tests inject dependency, activation, and startup failures. Offline process
tests create disposable Python environments and exercise handoff, rollback, and
later-launch environment selection without downloading models or running CODA
against hardware. CI runs the tests on Windows and Ubuntu with Python 3.11/3.12.
Set `CODA_RUN_UPDATE_ENV_SMOKE=1` to additionally create a real environment with
pip and verify an offline requirements install plus `pip check`.
