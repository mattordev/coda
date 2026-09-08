"""Select a published stable release and pin its source to a Git commit."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote

import requests
from semantic_version import Version

REPOSITORY = "mattordev/coda"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
REQUEST_TIMEOUT = (5, 15)
DISCOVERY_SECONDS = 45
METADATA_BYTES = 1024 * 1024
ARCHIVE_BYTES = 128 * 1024 * 1024
ARCHIVE_SECONDS = 180
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


def stable_version(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError("A stable semantic version is required")
    version = Version(value)
    if version.prerelease or version.build:
        raise ValueError("Automatic updates require a stable version without build metadata")
    return version


def read_installed_version(root):
    """Never invent or write version metadata for an unknown installation."""
    path = Path(root) / "version.json"
    if path.stat().st_size > 4096:
        raise ValueError("Local version.json is unexpectedly large; update manually")
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Local version.json must contain a version object")
    return stable_version(data.get("version"))


@dataclass(frozen=True)
class Release:
    tag: str
    version: Version
    commit: str

    def __post_init__(self):
        if not isinstance(self.tag, str) or not isinstance(self.commit, str):
            raise ValueError("Release tag and commit must be strings")
        tag_version = self.tag[1:] if self.tag.startswith("v") else self.tag
        if stable_version(tag_version) != self.version or not SHA_PATTERN.fullmatch(self.commit):
            raise ValueError("Invalid release identity")

    @property
    def archive_url(self):
        # No mutable branch, tag, or server-supplied download URL is used here.
        return f"https://codeload.github.com/{REPOSITORY}/zip/{self.commit}"

    @property
    def archive_root(self):
        return f"coda-{self.commit}"


def response_chunks(url, *, byte_limit, deadline, headers=None):
    """Bound inactivity, response size and elapsed time between read chunks."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Update request exceeded its time budget")
    timeout = tuple(min(value, remaining) for value in REQUEST_TIMEOUT)
    headers = {**(headers or {}), "Accept-Encoding": "identity"}
    with requests.get(url, stream=True, timeout=timeout, headers=headers, allow_redirects=False) as response:
        if response.status_code != 200:
            # Do not echo arbitrary remote error bodies or redirect elsewhere.
            raise ValueError(f"Update request returned HTTP {response.status_code}")
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("Unexpected compressed update response")
        length = response.headers.get("Content-Length")
        if length is not None and (int(length) < 0 or int(length) > byte_limit):
            raise ValueError("Update response exceeds its size limit")
        received = 0
        for chunk in response.iter_content(chunk_size=16384):
            if time.monotonic() >= deadline:
                raise TimeoutError("Update request exceeded its time budget")
            received += len(chunk)
            if received > byte_limit:
                raise ValueError("Update response exceeds its size limit")
            if chunk:
                yield chunk
        if length is not None and received != int(length):
            raise ValueError("Update response was truncated")


def _metadata(path, deadline):
    payload = b"".join(response_chunks(
        f"{API_ROOT}/{path}", byte_limit=METADATA_BYTES, deadline=deadline,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    ))
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Malformed release metadata")
    return data


def latest_release():
    deadline = time.monotonic() + DISCOVERY_SECONDS
    release = _metadata("releases/latest", deadline)
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("Latest release is not a published stable release")
    if not release.get("published_at"):
        raise ValueError("Latest release has no publication timestamp")
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        raise ValueError("Latest release has no tag")
    version = stable_version(tag[1:] if tag.startswith("v") else tag)
    reference = _metadata(f"git/ref/tags/{quote(tag, safe='')}", deadline)
    if reference.get("ref") != f"refs/tags/{tag}":
        raise ValueError("GitHub returned a different release tag")
    obj = reference.get("object")
    for _ in range(6):
        if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str) or not SHA_PATTERN.fullmatch(obj["sha"]):
            raise ValueError("Release tag has no valid Git object")
        if obj.get("type") == "commit":
            return Release(tag, version, obj["sha"])
        if obj.get("type") != "tag" or _ == 5:
            raise ValueError("Release tag does not resolve to a commit within five tag objects")
        obj = _metadata(f"git/tags/{obj['sha']}", deadline).get("object")
    raise ValueError("Release tag could not be resolved")


def download_archive(workspace, filename, release):
    with (workspace / filename).open("xb") as handle:
        for chunk in response_chunks(
            release.archive_url, byte_limit=ARCHIVE_BYTES,
            deadline=time.monotonic() + ARCHIVE_SECONDS,
        ):
            handle.write(chunk)


def validate_release_tag(root, tag):
    """Release-time check: fail before publishing inconsistent source metadata."""
    if stable_version(tag[1:] if tag.startswith("v") else tag) != read_installed_version(root):
        raise ValueError("Release tag does not match tracked version.json")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m utils.update_releases <release-tag>")
    validate_release_tag(Path(__file__).resolve().parents[1], sys.argv[1])
