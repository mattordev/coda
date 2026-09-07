# Updater recovery work

The source activation and rollback changes address #128. Continue updating
manually until dependency installation/restart (#129) and release selection
and end-to-end validation (#130) are complete.

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
  Failures return `False` to the caller, including rollback failures.

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

This step still downloads the main-branch source archive. It does not install
dependencies, restart CODA, or remove files deleted upstream. A successful return
means source activation completed, not that a full release upgrade was validated.
