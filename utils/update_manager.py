import json
import platform
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests

try:
    import pyuac
except ImportError:
    pyuac = None
from colorama import Fore, init
from tqdm import tqdm

# Updates CODA to the latest version, if available, directly from the GitHub repository.
# Keeping these up top so its easy to swap to releases later.
REPO_ZIP_URL = "https://github.com/mattordev/coda/archive/main.zip"
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


def update_program(workspace):
    # Pull latest source zip from GitHub.
    with requests.get(REPO_ZIP_URL, stream=True, timeout=30) as response:
        response.raise_for_status()
        with (workspace / ZIP_NAME).open("wb") as handle:
            for data in tqdm(response.iter_content(chunk_size=8192)):
                if data:
                    handle.write(data)


def extract_download(workspace):
    # Extract into a temp directory first, never directly into root.
    # Validate every member path to prevent Zip Slip directory traversal.
    extract_path = workspace / EXTRACT_DIR
    extract_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(workspace / ZIP_NAME, "r") as zip_ref:
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


def setup_updated_program(workspace):
    # Move extracted project into our staging folder.
    src = workspace / EXTRACT_DIR / "coda-main"
    dst = workspace / NEW_VERSION_DIR
    if not src.exists():
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


def validate_new_version(workspace):
    # Minimal sanity check so we do not activate junk downloads.
    staged = workspace / NEW_VERSION_DIR
    _validate_tree(staged)
    for name in ("main.py", "requirements.txt"):
        if not _safe_child(staged, name).is_file():
            raise FileNotFoundError(f"Downloaded update is missing {name}")
    for item in staged.iterdir():
        if _reserved(item.name):
            raise ValueError(f"Update contains reserved path: {item.name}")
    version_file = staged / "version.json"
    if not version_file.exists():
        raise FileNotFoundError("Downloaded update does not include version.json")

    with open(version_file, "r", encoding="utf-8") as json_file:
        json_data = json.load(json_file)
        if not isinstance(json_data.get("version"), str) or not json_data["version"].strip():
            raise ValueError("Downloaded version.json has no valid version string")


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
    except Exception:
        rollback_from_backup(root, workspace, installed, saved)
        raise


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


def main(install_root=None):
    """Stage within the install directory and return explicit success/failure."""
    root = Path(install_root) if install_root is not None else INSTALL_ROOT
    workspace = None
    try:
        _reject_links(root)
        root = root.resolve(strict=True)
        if not root.is_dir() or not (root / "main.py").is_file():
            raise ValueError("Update target is not a CODA installation")
        # Git checkouts include worktrees (.git can be a file) and nested installs.
        # Never replace development work with a downloaded release snapshot.
        if any((parent / ".git").exists() for parent in (root, *root.parents)):
            raise ValueError(
                "Automatic source updates are disabled in Git checkouts. "
                "Use Git to update this installation after saving your work."
            )
        workspace = Path(tempfile.mkdtemp(prefix=".coda-update-", dir=root))
        update_program(workspace)
        extract_download(workspace)
        setup_updated_program(workspace)
        validate_new_version(workspace)
        preserve_local_files(root, workspace)
        activate_updated_program(root, workspace)
    except Exception as e:
        print(f"Error during update: {e}")
        if workspace is not None:
            print(f"Update recovery files retained at {workspace}")
        return False

    init(autoreset=True)
    print(Fore.GREEN + "Source update complete!")
    print(Fore.BLUE + f"Previous files retained at {workspace / BACKUP_DIR}")
    print("Install the updated requirements and restart CODA before continuing.")
    return True


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

