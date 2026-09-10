# CODA update sandbox

This developer tool exercises CODA's real update preparation, dependency installation,
source activation, restart handoff, and later environment selection in a disposable
non-Git installation. It never lowers the version or runs the automatic updater in the
working repository.

## Interactive use

Close other sandbox CODA processes, then run from the repository root:

```powershell
& .\.venv\Scripts\python.exe .\tool\update_sandbox\update_sandbox.py
```

The wizard explains and confirms each step. On first use it asks for:

- the disposable starting version;
- the target version and full Git commit SHA;
- whether to copy local `.env`, `wakewords.json`, and `commands.json`;
- confirmation before creating and launching the sandbox.

On later use it detects the existing sandbox and offers `run`, `reset`, `status`,
`clean`, or `exit`. Reset and clean always require confirmation in the wizard.

The default location is `%TEMP%\coda-update-sandbox`. The tool refuses destinations
outside the system temp directory or whose directory name does not start with
`coda-update-sandbox`.

The tool does not depend on the repository's active environment having CODA's current
requirements. On the first run it offers to create `launcher-environment` inside the
sandbox, install the seed's pinned requirements, run `pip check`, and automatically
re-enter through that interpreter. This environment is retained across resets and is
removed with the sandbox during cleanup.

Launcher environment creation, dependency installation, and verification use blue
estimated progress bars. Detailed pip output is written to
`%TEMP%\coda-update-sandbox\launcher-dependencies.log`; failures identify that log
without flooding the terminal with package output.

## What the choices mean

- **run** starts manual mode and selects the manifest's exact commit instead of the
  latest published GitHub release. Enter `y` at CODA's update prompt.
- **reset** deletes only the sandbox installation and its generated update attempts,
  then restores the committed seed. Configuration copied during creation is restored.
- **status** reports versions, target commit, attempt count, and configuration status.
- **clean** permanently removes the manifest-owned sandbox and all environments in it.

The target commit must be pushed to GitHub before `run`, because production update code
downloads the immutable archive from GitHub. The seed is produced from committed `HEAD`
with `git archive`; uncommitted working-tree changes are deliberately excluded.

## Non-interactive commands

```powershell
& .\.venv\Scripts\python.exe .\tool\update_sandbox\update_sandbox.py create `
  --from-version 1.4.3 `
  --target-version 1.4.4 `
  --target-commit FULL_40_CHARACTER_SHA `
  --copy-config

& .\.venv\Scripts\python.exe .\tool\update_sandbox\update_sandbox.py run
& .\.venv\Scripts\python.exe .\tool\update_sandbox\update_sandbox.py status
& .\.venv\Scripts\python.exe .\tool\update_sandbox\update_sandbox.py reset
& .\.venv\Scripts\python.exe .\tool\update_sandbox\update_sandbox.py clean
```

Non-interactive `reset` and `clean` assume intentional use but retain all path and
manifest safety checks.

## Safety and limitations

- Never publish or share a sandbox containing copied configuration or dependency logs.
- `run` requires CODA's current development dependencies; this is a validation tool,
  not the proposed dependency-free `--fresh-install` bootstrap.
- Close sandbox CODA before reset or clean. Locked files cause the operation to fail.
- A sandbox validates the selected commit and local machine. It does not prove that a
  release tag has been published or that every supported platform has passed CI.
