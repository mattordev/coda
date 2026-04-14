import json
import platform
import shutil
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
# User/local runtime files we do not want to blow away on update.
PRESERVED_FILES = (
    ".env",
    "ELapi_key.txt",
    "ELapikey.txt",
    "wakewords.json",
    "commands.json",
)
# Local-only directories that should never be moved into the backup (e.g.
# virtualenvs, byte-code caches). Backing these up is slow and serves no purpose
# since they are always recreated from scratch.
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


def update_program():
    # Pull latest source zip from GitHub.
    response = requests.get(REPO_ZIP_URL, stream=True, timeout=30)
    response.raise_for_status()

    with open(ZIP_NAME, "wb") as handle:
        for data in tqdm(response.iter_content(chunk_size=8192)):
            if data:
                handle.write(data)


def extract_download():
    # Extract into a temp directory first, never directly into root.
    # Validate every member path to prevent Zip Slip directory traversal.
    extract_path = Path(EXTRACT_DIR).resolve()
    extract_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_NAME, "r") as zip_ref:
        for member in zip_ref.infolist():
            member_path = (extract_path / member.filename).resolve()
            try:
                member_path.relative_to(extract_path)
            except ValueError:
                raise ValueError(
                    f"Unsafe zip entry rejected: {member.filename!r} "
                    f"(resolved to {member_path})"
                )
            zip_ref.extract(member, extract_path)


def setup_updated_program():
    # Move extracted project into our staging folder.
    src = Path(EXTRACT_DIR) / "coda-main"
    dst = Path(NEW_VERSION_DIR)
    if not src.exists():
        raise FileNotFoundError(f"Expected extracted source at: {src}")

    if dst.exists():
        # Stale previous attempt, clear it out.
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    for filename in src.iterdir():
        shutil.move(str(filename), str(dst / filename.name))

    # Cleanup download artifacts once staging is ready.
    shutil.rmtree(EXTRACT_DIR, ignore_errors=True)
    Path(ZIP_NAME).unlink(missing_ok=True)


def preserve_local_files():
    # Copy over local files that are environment/user specific.
    for filename in PRESERVED_FILES:
        src = Path(filename)
        dst = Path(NEW_VERSION_DIR) / filename
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


def validate_new_version():
    # Minimal sanity check so we do not activate junk downloads.
    version_file = Path(NEW_VERSION_DIR) / "version.json"
    if not version_file.exists():
        raise FileNotFoundError("Downloaded update does not include version.json")

    with open(version_file, "r", encoding="utf-8") as json_file:
        json_data = json.load(json_file)
        if "version" not in json_data:
            raise KeyError("Downloaded version.json is missing a 'version' key")


def activate_updated_program():
    # Swap old -> backup, staged -> live.
    root = Path.cwd()
    backup = root / BACKUP_DIR
    new_version = root / NEW_VERSION_DIR

    if backup.exists():
        # Keep only one backup to avoid piling up old copies.
        shutil.rmtree(backup)

    old_items = [
        p for p in root.iterdir()
        if p.name not in {".git", BACKUP_DIR, NEW_VERSION_DIR} | EXCLUDED_FROM_BACKUP
    ]

    backup.mkdir(parents=True, exist_ok=True)
    for item in old_items:
        shutil.move(str(item), str(backup / item.name))

    for item in new_version.iterdir():
        shutil.move(str(item), str(root / item.name))

    new_version.rmdir()


def rollback_from_backup():
    # Best effort rollback if activation fails mid-way.
    root = Path.cwd()
    backup = root / BACKUP_DIR
    if not backup.exists():
        return

    current_items = [
        p for p in root.iterdir() if p.name not in {".git", BACKUP_DIR, NEW_VERSION_DIR}
    ]
    for item in current_items:
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)

    for item in backup.iterdir():
        shutil.move(str(item), str(root / item.name))


def main():
    # Ordered flow. If anything breaks, rollback_from_backup() kicks in.
    try:
        update_program()
        extract_download()
        setup_updated_program()
        preserve_local_files()
        validate_new_version()
        activate_updated_program()
    except Exception as e:
        print(f"Error during update: {e}")
        rollback_from_backup()
        return

    init(autoreset=True)
    print(Fore.GREEN + "Update complete!")
    print(Fore.BLUE + "Old version moved to coda-old-version.")


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
