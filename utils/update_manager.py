import json
from dataclasses import dataclass
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests

try:
    import pyuac
except ImportError:
    pyuac = None
from colorama import Fore, init

from utils.update_bootstrap import (
    ACTIVE_ENVIRONMENT, HANDOFF_ENV, launch_environment,
    managed_environment, wait_for_process,
)
from utils.update_dependencies import prepare_environment
from utils.update_releases import latest_release, read_installed_version, download_archive
from utils.update_progress import UpdateProgress
from utils.update_releases import ARCHIVE_SECONDS

# Temp download/extract folders used during update.
ZIP_NAME = "coda.zip"
EXTRACT_DIR = "coda"
NEW_VERSION_DIR = "coda-version-new"
# Where we park the old install before switching over.
BACKUP_DIR = "coda-old-version"
INSTALL_ROOT = Path(__file__).resolve().parents[1]
# User/local runtime files we do not want to blow away on update.
PRESERVED_FILES = (
    ".env",
    "wakewords.json",
    "commands.json",
)
# Local-only directories that updates must never replace or move.
EXCLUDED_FROM_BACKUP = frozenset({
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "node_modules",
    "dist",
    "build",
})


def update_program(workspace, release):
    download_archive(workspace, ZIP_NAME, release)


def extract_download(workspace):
    # Extract into a temp directory first, never directly into root.
    # Validate every member path to prevent Zip Slip directory traversal.
    extract_path = workspace / EXTRACT_DIR
    extract_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(workspace / ZIP_NAME, "r") as zip_ref:
        if len(zip_ref.infolist()) > 10000 or sum(item.file_size for item in zip_ref.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Downloaded archive exceeds extraction limits")
        for member in zip_ref.infolist():
            member_path = (extract_path / member.filename).resolve()
            try:
                member_path.relative_to(extract_path)
            except ValueError:
                raise ValueError(
                    f"Unsafe zip entry rejected: {member.filename!r} "
                    f"(resolved to {member_path})"
                )
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"Archive contains a symlink: {member.filename!r}")
        zip_ref.extractall(extract_path)


def setup_updated_program(workspace, release):
    # Move extracted project into our staging folder.
    extracted = workspace / EXTRACT_DIR
    src = extracted / release.archive_root
    dst = workspace / NEW_VERSION_DIR
    if not src.is_dir() or list(extracted.iterdir()) != [src]:
        raise FileNotFoundError(f"Expected extracted source at: {src}")

    dst.mkdir()

    for filename in src.iterdir():
        shutil.move(str(filename), str(dst / filename.name))

    # Retain staging artifacts for inspection and recovery.


def preserve_local_files(root, workspace):
    # Copy over local files that are environment/user specific.
    for filename in PRESERVED_FILES:
        src = _safe_child(root, filename)
        dst = _safe_child(workspace / NEW_VERSION_DIR, filename)
        if src.exists():
            if not src.is_file():
                raise ValueError(f"Expected a local configuration file: {src}")
            shutil.copy2(src, dst)


def validate_new_version(workspace, release):
    # Minimal sanity check so we do not activate junk downloads.
    staged = workspace / NEW_VERSION_DIR
    _validate_tree(staged)
    for name in ("main.py", "requirements.txt"):
        if not _safe_child(staged, name).is_file():
            raise FileNotFoundError(f"Downloaded update is missing {name}")
    for item in staged.iterdir():
        if _reserved(item.name):
            raise ValueError(f"Update contains reserved path: {item.name}")
    if read_installed_version(staged) != release.version:
        raise ValueError("Downloaded version.json does not match the selected release")


def _reject_links(path):
    if path.is_symlink() or (
        path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400
    ):
        raise ValueError(f"Update path must not be a link or junction: {path}")


def _validate_tree(path):
    _reject_links(path)
    if path.is_dir():
        for child in path.iterdir():
            _validate_tree(child)


def _reserved(name):
    return (
        name.lower() in EXCLUDED_FROM_BACKUP | {".git", BACKUP_DIR, NEW_VERSION_DIR}
        or name.lower().startswith(".coda-update-")
    )


def _safe_child(parent, name):
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError(f"Invalid update entry: {name!r}")
    child = parent / name
    _reject_links(child)
    if child.resolve().parent != parent.resolve():
        raise ValueError(f"Update path escapes its directory: {child}")
    return child


def activate_updated_program(root, workspace):
    """Replace staged entries; retain unrelated local files and old backups."""
    backup = workspace / BACKUP_DIR
    staged = workspace / NEW_VERSION_DIR
    names = [item.name for item in staged.iterdir()]
    for name in names:
        if _reserved(name):
            raise ValueError(f"Cannot replace reserved path: {name}")
        _validate_tree(_safe_child(root, name))
        _validate_tree(_safe_child(staged, name))

    backup.mkdir()
    saved = []
    installed = []
    try:
        for name in names:
            target = _safe_child(root, name)
            if target.exists():
                target.rename(_safe_child(backup, name))
                saved.append(name)
            _safe_child(staged, name).rename(target)
            installed.append(name)
    except BaseException:
        rollback_from_backup(root, workspace, installed, saved)
        raise
    return installed, saved


def rollback_from_backup(root, workspace, installed, saved):
    """Undo only moves recorded by this activation, without deleting files."""
    staged = workspace / NEW_VERSION_DIR
    backup = workspace / BACKUP_DIR
    errors = []
    for name in reversed(installed):
        try:
            _safe_child(root, name).rename(_safe_child(staged, name))
        except OSError as error:
            errors.append(f"{name}: {error}")
    for name in reversed(saved):
        try:
            target = _safe_child(root, name)
            if target.exists():
                raise FileExistsError(f"Cannot restore over remaining updated entry: {target}")
            _safe_child(backup, name).rename(target)
        except OSError as error:
            errors.append(f"{name}: {error}")
    if errors:
        raise OSError("Rollback incomplete; recovery files retained: " + "; ".join(errors))


@dataclass(frozen=True)
class PreparedUpdate:
    """A fully staged update, not yet activated in the running installation."""

    root: Path
    workspace: Path
    python: Path
    arguments: tuple[str, ...]


def _validate_install_root(root):
    _reject_links(root)
    root = root.resolve(strict=True)
    if not root.is_dir() or not (root / "main.py").is_file():
        raise ValueError("Update target is not a CODA installation")
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise ValueError(
            "Automatic source updates are disabled in Git checkouts. "
            "Use Git to update this installation after saving your work."
        )
    return root


def prepare_update(install_root=None, arguments=None, release=None):
    """Stage source and dependencies without changing the live installation."""
    root = Path(install_root) if install_root is not None else INSTALL_ROOT
    workspace = None
    try:
        # Git checkouts include worktrees (.git can be a file) and nested installs.
        # Never replace development work with a downloaded release snapshot.
        root = _validate_install_root(root)
        installed = read_installed_version(root)
        release = release if release is not None else latest_release()
        if release.version <= installed:
            return None
        workspace = Path(tempfile.mkdtemp(prefix=".coda-update-", dir=root))
        # Record the exact confirmed identity for recovery; do not rediscover latest.
        (workspace / "release.json").write_text(json.dumps({
            "tag": release.tag, "version": str(release.version), "commit": release.commit,
        }), encoding="utf-8")
        with UpdateProgress("Downloading update", budget=ARCHIVE_SECONDS):
            update_program(workspace, release)
        with UpdateProgress("Extracting and validating update"):
            extract_download(workspace)
            setup_updated_program(workspace, release)
            validate_new_version(workspace, release)
            preserve_local_files(root, workspace)
        staged = workspace / NEW_VERSION_DIR
        # Older releases cannot participate in the supervised startup handshake.
        if not (staged / "utils" / "update_bootstrap.py").is_file():
            raise ValueError("Downloaded release does not support safe restart; update manually")
        python = prepare_environment(workspace, staged, sys.executable)
    except Exception as e:
        print(f"Error during update: {e}")
        if workspace is not None:
            print(f"Update recovery files retained at {workspace}")
        return None

    return PreparedUpdate(
        root, workspace, python,
        tuple(sys.argv[1:] if arguments is None else arguments),
    )


def check_for_update(install_root=None):
    """Startup discovery: fail closed and never prompt a development checkout."""
    root = Path(install_root) if install_root is not None else INSTALL_ROOT
    try:
        root = _validate_install_root(root)
        installed = read_installed_version(root)
        release = latest_release()
        if release.version <= installed:
            return None
        prompt = input(f"CODA {release.tag} is available. Update from {installed}? (y/n): ")
        if prompt.strip().lower() == "y":
            return prepare_update(root, release=release)
    except (OSError, ValueError, requests.RequestException, EOFError) as error:
        print(f"Automatic update skipped: {error}")
    return None


def _stop_failed_child(child):
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=6)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=6)


def restart_candidate(update):
    """Launch a child which waits for acceptance before running any workers."""
    env = launch_environment(update.python)
    env[HANDOFF_ENV] = update.workspace.name
    return subprocess.Popen(
        [str(update.python), str(update.root / "main.py"), *update.arguments],
        cwd=update.root, env=env,
    )


def wait_for_candidate(update, child, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError("Updated CODA exited before startup was ready")
        if (update.workspace / "restart-ready").is_file():
            return
        time.sleep(0.05)
    raise TimeoutError("Updated CODA did not acknowledge startup in time")


def complete_update(update):
    """Activate only after old-runtime shutdown; roll back failed startup."""
    root, workspace = update.root, update.workspace
    journal = None
    child = None
    selection_changed = False
    previous_selection = None
    marker = None
    try:
        # Staging can take minutes: repeat the development-checkout guard at
        # the activation boundary, and bind recovery to this exact attempt.
        _validate_install_root(root)
        if _safe_child(root, workspace.name) != workspace:
            raise ValueError("Prepared workspace is outside the installation")
        marker = _safe_child(root, ACTIVE_ENVIRONMENT)
        previous_selection = marker.read_bytes() if marker.exists() else None
        if previous_selection is not None:
            with (workspace / "previous-environment.json").open("xb") as handle:
                handle.write(previous_selection)
        # Verify the prepared interpreter is exactly the environment this attempt owns.
        relative = str(Path(workspace.name) / "environment")
        if managed_environment(root, relative) != update.python:
            raise ValueError("Prepared interpreter is outside its managed environment")
        journal = activate_updated_program(root, workspace)
        child = restart_candidate(update)
        wait_for_candidate(update, child)
        selection = workspace / ACTIVE_ENVIRONMENT
        selection.write_text(json.dumps({"environment": relative}), encoding="utf-8")
        selection.replace(marker)
        selection_changed = True
        with (workspace / "restart-proceed").open("x", encoding="utf-8") as handle:
            handle.write("proceed")
    except BaseException as error:
        # Never restore sources underneath a child that might still be running.
        if child is not None:
            try:
                _stop_failed_child(child)
            except (OSError, subprocess.TimeoutExpired) as stop_error:
                print(f"Cannot stop updated CODA; manual recovery required: {stop_error}")
                print(f"Recovery files retained at {workspace}")
                return None
        try:
            if selection_changed:
                if previous_selection is None:
                    marker.unlink()
                else:
                    restored = workspace / "previous-environment.json"
                    restored.write_bytes(previous_selection)
                    restored.replace(marker)
            if journal is not None:
                rollback_from_backup(root, workspace, *journal)
        except (OSError, ValueError) as recovery_error:
            print(f"Automatic recovery incomplete: {recovery_error}")
        print(f"Update activation/restart failed: {error}")
        print(f"Recovery files retained at {workspace}")
        return None

    init(autoreset=True)
    print(Fore.GREEN + "Update activated; handing over to the new runtime.")
    print(Fore.BLUE + f"Previous files retained at {workspace / BACKUP_DIR}")
    return child


def main(install_root=None):
    """Standalone startup-only update; never overwrite a running environment."""
    update = prepare_update(install_root, arguments=())
    if update is None:
        return False
    child = complete_update(update)
    if child is None:
        return False
    return wait_for_process(child) == 0


def _can_attempt_uac_elevation():
    # UAC is Windows-only, keep Linux/macOS path clean.
    return platform.system().lower() == "windows"


if __name__ == "__main__":
    if _can_attempt_uac_elevation() and pyuac is not None and not pyuac.isUserAdmin():
        print("Admin permissions NOT FOUND")
        print("Re-launching as admin!")
        pyuac.runAsAdmin()
    else:
        main()

