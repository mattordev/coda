"""Standalone filesystem MCP with separate, explicit read and write permissions."""

import argparse
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from os import listdir
from os.path import isdir, isfile, join
from pathlib import Path, PureWindowsPath
import json
from fnmatch import fnmatchcase
import stat
from datetime import datetime, timezone
import codecs
import os
import re
import hashlib
import uuid
import sys
from time import monotonic
from collections import deque


MAX_READ_BYTES = 1024 * 1024
DEFAULT_RANGE_BYTES = 64 * 1024
SEARCH_SECONDS = 5
SEARCH_OUTPUT_CHARS = 32768


# Locate the config file alongside server.py
CONFIG_PATH = Path(__file__).resolve().parent / "filesystem.config.json"
OPERATOR_PATHS = ()  # Host registry provenance, supplied only at process launch.

server = MCPServer("coda-filesystem", log_level="ERROR")


class FilesystemPermissionError(PermissionError):
    """Keep a machine-readable reason alongside the diagnostic message."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@server.tool(structured_output=False)
def list_directory(path: str) -> CallToolResult:
    """List the contents of a directory."""
    try:
        entries = directory_entries(path)
    except FilesystemPermissionError as error:
        return error_result(error.code, str(error))
    except PermissionError as error:
        return error_result("permission_denied", str(error))
    except NotADirectoryError as error:
        return error_result("not_a_directory", str(error))
    except FileNotFoundError as error:
        return error_result("not_found", str(error))
    text = "\n".join(
        f"[{'FILE' if entry['type'] == 'file' else 'DIR'}] {entry['name']}" for entry in entries
    )
    return CallToolResult(
        content=[TextContent(type="text", text=text or "No accessible entries were returned.")],
        structuredContent={"kind": "directory_listing", "entries": entries},
    )
    
@server.tool(structured_output=False)
def read_file(path: str, offset_bytes: int = 0, max_bytes: int | None = None,
              include_revision: bool = False) -> CallToolResult:
    """Read permitted UTF-8 text; optionally select a bounded byte range.

    Without range arguments, preserve the complete-file 1 MiB limit. Supplying
    max_bytes (1 to 1048576) or a nonzero offset selects a range; the default
    range budget is 65536 bytes. Resume using returned next_offset_bytes, which
    never splits a UTF-8 character or CRLF pair. Ranges do not grant permission.
    include_revision adds the original-byte SHA-256 to a complete read and
    preserves exact UTF-8/newlines for a subsequent conflict-checked mutation.
    """
    
    try:
        if not isinstance(path, str) or not path.strip() or not Path(path).is_absolute():
                raise FilesystemPermissionError("invalid_arguments", "Please supply an absolute file path.")
        if type(offset_bytes) is not int or not 0 <= offset_bytes <= 2**63 - 1:
            raise FilesystemPermissionError("invalid_arguments", "The byte offset must be a nonnegative 64-bit integer.")
        if max_bytes is not None and (type(max_bytes) is not int or not 1 <= max_bytes <= MAX_READ_BYTES):
            raise FilesystemPermissionError("invalid_arguments", "The byte budget must be an integer from 1 to 1048576.")
        if type(include_revision) is not bool or (include_revision and (offset_bytes or max_bytes is not None)):
            raise FilesystemPermissionError('invalid_arguments', 'Revision reads require a complete file without range arguments.')
        config = load_config()
        check_if_path_is_allowed(config, path)
        check_if_path_is_blocked(config, path)
    except FilesystemPermissionError as error:
        return error_result(error.code, str(error))
    except PermissionError as error:
        return error_result("permission_denied", str(error))
    except NotADirectoryError as error:
        return error_result("not_a_directory", str(error))
    except FileNotFoundError as error:
        return error_result("not_found", str(error))
    
    requested_path = get_full_path(path)
    # make sure the file exists
    is_file = requested_path.is_file()
    
    if is_file:
        # do file reading
        try: 
            if offset_bytes or max_bytes is not None:
                return read_file_range(requested_path, offset_bytes, max_bytes or DEFAULT_RANGE_BYTES)
            if include_revision:
                raw = _read_safe_bytes(requested_path)
                text = raw.decode('utf-8')
            else:
                text = read_safe_file(requested_path)
        except FilesystemPermissionError as error:
            return error_result(error.code, str(error))
        except UnicodeDecodeError as error:
            return error_result("invalid_encoding", f"Not the correct file type: {error}")
        except ValueError as error:
            return error_result("file_too_large", str(error))
        except OSError as error:
            return error_result("os_error", str(error))
        return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structuredContent=({'kind': 'file_contents', 'sha256': hashlib.sha256(raw).hexdigest(),
                                    'byte_length': len(raw)} if include_revision else {"kind": "file_contents"}),
            )
    elif requested_path.is_dir():
        return error_result("is_a_directory", f"{requested_path} is a directory, not a file.")
    else:
        return error_result("file_not_found", f"File not found at: {requested_path}")

        

def read_file_range(path: Path, offset_bytes: int, max_bytes: int) -> CallToolResult:
    """Seek without scanning preceding content; read at most budget + 1 bytes."""
    with path.open("rb") as source:
        source.seek(0, 2)
        if offset_bytes > source.tell():
            raise FilesystemPermissionError("invalid_arguments", "The byte offset is beyond the end of the file.")
        source.seek(offset_bytes)
        data = source.read(max_bytes + 1)
    if offset_bytes and data and data[0] & 0xC0 == 0x80:
        raise FilesystemPermissionError("invalid_arguments", "The byte offset must begin at a UTF-8 character boundary.")
    # Decode only the selected bytes. An unfinished final character is retained
    # for the next range, but malformed UTF-8 and incomplete EOF still fail.
    chunk = data[:max_bytes]
    decoder = codecs.getincrementaldecoder("utf-8")()
    text = decoder.decode(chunk, final=len(data) <= max_bytes)
    consumed = len(chunk) - len(decoder.getstate()[0])
    # Keeping CRLF together avoids producing two newlines across continuations.
    if consumed and data[consumed - 1:consumed + 1] == b"\r\n":
        consumed -= 1
        text = text[:-1]
    has_more = len(data) > consumed
    if has_more and consumed == 0:
        raise FilesystemPermissionError("invalid_arguments", "The byte budget is too small for the next character or newline. Increase max_bytes.")
    next_offset = offset_bytes + consumed if has_more else None
    partial = bool(offset_bytes or has_more)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if partial:
        continuation = f" Continue at byte {next_offset}." if has_more else " End of file."
        text = f"[Partial file content: bytes {offset_bytes} to {offset_bytes + consumed} (end exclusive).{continuation}]\n" + text
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={
            "kind": "file_contents", "partial": partial, "offset_bytes": offset_bytes,
            "bytes_read": consumed, "has_more": has_more, "next_offset_bytes": next_offset,
        },
    )


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
))
def create_file(path: str, content: str = "") -> CallToolResult:
    """Create a new UTF-8 file at an absolute path; never overwrite an existing path.

    The parent directory must already exist. Content is limited to 1 MiB of UTF-8
    bytes. Coda requires explicit write permissions and confirmation before use.
    """
    try:
        _protect_transaction_paths(_concrete_path(path))
        if not isinstance(content, str):
            raise FilesystemPermissionError("invalid_arguments", "File content must be UTF-8 text.")
        try:
            data = content.encode("utf-8")
        except UnicodeError as error:
            raise FilesystemPermissionError("invalid_arguments", "File content must be UTF-8 text.") from error
        if len(data) > 1024 * 1024:
            raise FilesystemPermissionError("content_too_large", "New file content must be at most 1 MiB of UTF-8 bytes.")
        target = creation_path(path)
        # The earlier existence check improves errors, but is not enough on its
        # own: exclusive creation also rejects a competing creator atomically.
        with target.open("xb") as output:
            output.write(data)
    except FilesystemPermissionError as error:
        return error_result(error.code, str(error))
    except FileExistsError:
        return error_result("already_exists", "That path already exists. Nothing was overwritten.")
    except FileNotFoundError:
        return error_result("not_found", "The parent directory does not exist. Create it first.")
    except NotADirectoryError:
        return error_result("not_a_directory", "The parent path is not a directory.")
    except PermissionError as error:
        return error_result("permission_denied", str(error))
    except (OSError, RuntimeError) as error:
        return error_result("os_error", str(error))
    return creation_result(target, "file")


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
))
def create_directory(path: str) -> CallToolResult:
    """Create one new directory at an absolute path with an existing parent.

    Existing paths are never replaced. Coda requires explicit write permissions
    and confirmation before use; missing parent directories are not created.
    """
    try:
        _protect_transaction_paths(_concrete_path(path))
        target = creation_path(path)
        target.mkdir(parents=False, exist_ok=False)
    except FilesystemPermissionError as error:
        return error_result(error.code, str(error))
    except FileExistsError:
        return error_result("already_exists", "That path already exists. Nothing was replaced.")
    except FileNotFoundError:
        return error_result("not_found", "The parent directory does not exist. Create it first.")
    except NotADirectoryError:
        return error_result("not_a_directory", "The parent path is not a directory.")
    except PermissionError as error:
        return error_result("permission_denied", str(error))
    except (OSError, RuntimeError) as error:
        return error_result("os_error", str(error))
    return creation_result(target, "directory")


def _concrete_path(path):
    if not isinstance(path, str) or not path.strip() or len(path) > 32768 or not Path(path).is_absolute():
        raise FilesystemPermissionError("invalid_arguments", "Please supply an absolute concrete path.")
    try:
        path.encode('utf-8')
    except UnicodeError as error:
        raise FilesystemPermissionError('invalid_arguments', 'Paths must contain valid UTF-8 text.') from error
    target = Path(path)
    if len(target.parts) > 128:
        raise FilesystemPermissionError('invalid_arguments', 'Mutation paths are limited to 128 components.')
    # Reject ambiguous Windows names on both supported platforms, including
    # alternate data streams, device names and dot/space normalization tricks.
    if any(part in (".", "..") or part.endswith((" ", "."))
           or PureWindowsPath(part).is_reserved() or any(char in part for char in '<>:"|?*')
           or any(ord(char) < 32 for char in part) for part in target.parts[1:]):
        raise FilesystemPermissionError("invalid_arguments", "Please supply a concrete path without traversal or reserved names.")
    return target


def creation_path(path: str, *, allow_existing=False) -> Path:
    """Authorize an entry without borrowing any of the read root grants."""
    target = _concrete_path(path)
    _protect_operator_paths(target)
    config = load_config()
    write = config.get("write", {})
    if not isinstance(write, dict) or type(write.get("enabled", False)) is not bool:
        raise FilesystemPermissionError("configuration_error", "Write configuration is invalid.")
    if not write.get("enabled", False):
        raise FilesystemPermissionError("write_disabled", "Filesystem creation is disabled in the write configuration.")
    roots = write.get("allowed_roots", [])
    blocked = write.get("blocked_paths", [])
    if (not isinstance(roots, list) or any(not isinstance(root, str) or not root.strip()
            or not Path(root).is_absolute() or '\0' in root for root in roots)
            or not isinstance(blocked, list) or any(not isinstance(pattern, str) or not pattern.strip() for pattern in blocked)
            or type(write.get("require_confirmation", True)) is not bool):
        raise FilesystemPermissionError("configuration_error", "Write roots and blocked paths are invalid.")
    # Read-side blocks still protect sensitive names, but read roots never grant
    # write access. A separate write block list may restrict creation further.
    policy = {"read": {"allowed_roots": roots, "blocked_paths": config["read"]["blocked_paths"] + blocked}}
    check_if_path_is_allowed(policy, path)
    check_if_path_is_blocked(policy, path)
    for candidate in (target, *target.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if candidate.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise FilesystemPermissionError("blocked_path", "Creation through symbolic links or junctions is not permitted.")
    if target.exists() and not allow_existing:
        raise FilesystemPermissionError("already_exists", "That path already exists. Nothing was replaced.")
    return target


def creation_result(path: Path, entry_type: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"Created {entry_type}: {path}")],
        structuredContent={"kind": "filesystem_created", "path": str(path), "type": entry_type},
    )


def _read_safe_bytes(path: Path) -> bytes:
    """Bound the actual read, including files that grow after the size check."""
    max_file_size_b = MAX_READ_BYTES

    if path.stat().st_size > max_file_size_b:
        raise ValueError(f"{path.name} is too big to be read, please open it manually.")
    with path.open("rb") as source:
        data = source.read(max_file_size_b + 1)
    if len(data) > max_file_size_b:
        raise ValueError(f"{path.name} is too big to be read, please open it manually.")
    return data


def read_safe_file(path: Path) -> str:
    """Match universal newline handling without an unbounded text read."""
    return _read_safe_bytes(path).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def error_result(code, message):
    return CallToolResult(
        isError=True, content=[TextContent(type="text", text=message)],
        structuredContent={"error": {"code": code, "message": message}},
    )


def directory_entries(path: str) -> list[dict[str, str | float]]:
    """Validate once per request, then filter children using the same policy."""
    if not path.strip() or not Path(path).is_absolute():
        raise FilesystemPermissionError("invalid_arguments", "Please supply an absolute directory path.")
    config = load_config()
    check_if_path_is_allowed(config, path)
    check_if_path_is_blocked(config, path)
    requested_path = get_full_path(path)
    if not requested_path.is_dir():
        if not requested_path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        raise NotADirectoryError(f"Invalid directory: {path}")
    entries = sorted(listdir(requested_path), key=str.casefold)
    response = []
    for entry in entries:
        full_path = join(requested_path, entry)

        try:
            check_if_path_is_allowed(config, full_path)
            check_if_path_is_blocked(config, full_path)
            modified_at = Path(full_path).stat().st_mtime
        except (PermissionError, FileNotFoundError):
            continue

        if isfile(full_path):
            response.append({"name": entry, "type": "file", "modified_at": modified_at})
        elif isdir(full_path):
            response.append({"name": entry, "type": "directory", "modified_at": modified_at})

    return response

def get_permissions_from_config(config: dict):
    allowed_dirs = config['read']['allowed_roots']

    return allowed_dirs

def check_if_path_is_allowed(config: dict, path: str):
    allowed_dirs = get_permissions_from_config(config)
    requested_path = Path(path).resolve()

    is_allowed = False

    for allowed_dir in allowed_dirs:
        allowed_root = Path(allowed_dir).resolve()

        if requested_path.is_relative_to(allowed_root):
            is_allowed = True
            break

    if not is_allowed:
        raise FilesystemPermissionError("outside_allowed_roots", f"Coda is not permitted to read this folder: {path}. Please edit your config.")

def check_if_path_is_blocked(config: dict, path_to_check: str):
    blocked_paths = config['read']['blocked_paths']
    lexical_path = Path(path_to_check).absolute()
    requested_path = lexical_path.resolve()
    # Both names matter: a blocked link name must not bypass policy by resolving
    # to an otherwise allowed target, and a permitted name must not hide a blocked target.
    paths_to_check = [lexical_path, *lexical_path.parents, requested_path, *requested_path.parents]

    for pattern in blocked_paths:
        pattern = pattern.replace("\\", "/").casefold()

        for candidate in paths_to_check:
            candidate_string = candidate.as_posix().casefold()

            if (
                fnmatchcase(candidate_string, pattern)
                or (
                    pattern.startswith("**/")
                    and fnmatchcase(candidate_string, pattern[3:])
                )
            ):
                raise FilesystemPermissionError(
                    "blocked_path",
                    f"Coda is not permitted to access: {requested_path}"
                )

def load_config() -> dict:
    """Loads filesystem config, defines what can be accessed and what is restricted"""
    try:
        config_text = CONFIG_PATH.read_text(encoding="utf-8-sig")
        config = json.loads(config_text)
        read = config["read"]
        roots, patterns = read["allowed_roots"], read["blocked_paths"]
        if not isinstance(roots, list) or not isinstance(patterns, list):
            raise ValueError("Read permissions must be arrays.")
        if any(not isinstance(root, str) or not root.strip() or not Path(root).is_absolute() for root in roots):
            raise ValueError("Allowed roots must be absolute paths.")
        if any(not isinstance(pattern, str) or not pattern.strip() for pattern in patterns):
            raise ValueError("Blocked patterns must be non-empty strings.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise FilesystemPermissionError(
            "configuration_error", "Filesystem configuration is missing or invalid. Check filesystem.config.json."
        ) from error
    return config

def get_full_path(incomplete_path: str) -> Path:
    return Path(incomplete_path).resolve()


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
))
def get_file_metadata(path: str) -> CallToolResult:
    """Inspect type, file byte size and modification time without reading contents.

    Requires an absolute, read-authorised path. Directory/other entry sizes are
    omitted because their filesystem allocation size is not their content size.
    Modification time is Unix seconds in structured output and UTC in text;
    no portable creation timestamp is inferred from platform-specific ctime.
    """
    try:
        if not isinstance(path, str) or not path.strip() or "\0" in path or not Path(path).is_absolute():
            raise FilesystemPermissionError("invalid_arguments", "Please supply an absolute metadata path.")
        config = load_config()
        check_if_path_is_allowed(config, path)
        check_if_path_is_blocked(config, path)
        target = get_full_path(path)
        metadata = target.stat()
        kind = "file" if stat.S_ISREG(metadata.st_mode) else "directory" if stat.S_ISDIR(metadata.st_mode) else "other"
        size = metadata.st_size if kind == "file" else None
        modified = datetime.fromtimestamp(metadata.st_mtime, timezone.utc).isoformat(timespec="seconds")
    except FilesystemPermissionError as error:
        return error_result(error.code, str(error))
    except FileNotFoundError:
        return error_result("not_found", "That path does not exist.")
    except NotADirectoryError:
        return error_result("not_a_directory", "A parent path is not a directory.")
    except PermissionError:
        return error_result("permission_denied", "Metadata access was denied.")
    except (OSError, ValueError, RuntimeError, OverflowError):
        return error_result("os_error", "File metadata could not be inspected.")
    size_text = f" {size} bytes." if size is not None else ""
    return CallToolResult(
        content=[TextContent(type="text", text=f"{kind.capitalize()}: {target.name}.{size_text} Last modified: {modified}.")],
        structuredContent={
            "kind": "file_metadata", "path": str(target), "type": kind,
            "size_bytes": size, "modified_at": metadata.st_mtime,
        },
    )


def _search_policy(config, root):
    """Revocation discards observations rather than returning stale grants."""
    current = load_config()
    if current["read"] != config["read"]:
        raise FilesystemPermissionError("permission_denied", "Read permissions changed during the search. Request it again.")
    check_if_path_is_allowed(current, str(root))
    check_if_path_is_blocked(current, str(root))


def _search_tree(root, pattern, config, max_depth, max_entries, max_results):
    """Breadth-first traversal sorts only completely enumerated bounded folders."""
    pending = deque([(root, 0)])
    matches, reasons = [], set()
    examined = processed = output_chars = 0
    deadline = monotonic() + SEARCH_SECONDS
    stopped = False
    while pending and not stopped:
        directory, depth = pending.popleft()
        _search_policy(config, root)
        try:
            directory_stat = directory.lstat()
            if stat.S_ISLNK(directory_stat.st_mode) or getattr(directory_stat, "st_file_attributes", 0) & 0x400:
                reasons.add("unavailable_directory")
                continue
            check_if_path_is_allowed(config, str(directory))
            check_if_path_is_blocked(config, str(directory))
            if not directory.resolve().is_relative_to(root):
                reasons.add("unavailable_directory")
                continue
            children = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if monotonic() >= deadline:
                        reasons.add("time_limit")
                        stopped = True
                        break
                    examined += 1
                    if examined > max_entries:
                        reasons.add("entry_limit")
                        stopped = True
                        break
                    children.append(entry)
                    if examined % 64 == 0:
                        _search_policy(config, root)
            if stopped:
                # A capped directory's arbitrary OS enumeration prefix must not
                # decide which names match. Return only earlier complete folders.
                break
            for entry in sorted(children, key=lambda child: (child.name.casefold(), child.name)):
                if monotonic() >= deadline:
                    reasons.add("time_limit")
                    stopped = True
                    break
                processed += 1
                if processed % 64 == 0:
                    _search_policy(config, root)
                child = directory / entry.name
                try:
                    metadata = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                        continue
                    check_if_path_is_allowed(config, str(child))
                    check_if_path_is_blocked(config, str(child))
                    canonical = child.resolve()
                    if not canonical.is_relative_to(root):
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        if depth < max_depth:
                            pending.append((canonical, depth + 1))
                        else:
                            reasons.add("depth_limit")
                    elif stat.S_ISREG(metadata.st_mode) and fnmatchcase(entry.name.casefold(), pattern.casefold()):
                        match = {"name": entry.name, "path": str(canonical), "type": "file",
                                 "modified_at": metadata.st_mtime}
                        cost = len(json.dumps(match, ensure_ascii=True))
                        if output_chars + cost > SEARCH_OUTPUT_CHARS:
                            reasons.add("output_limit")
                            stopped = True
                            break
                        matches.append(match)
                        output_chars += cost
                        if len(matches) >= max_results:
                            reasons.add("result_limit")
                            stopped = True
                            break
                except FilesystemPermissionError:
                    continue
                except (OSError, RuntimeError):
                    reasons.add("unavailable_entry")
        except FilesystemPermissionError:
            raise
        except (OSError, RuntimeError):
            reasons.add("unavailable_directory")
    _search_policy(config, root)
    matches.sort(key=lambda match: (match["path"].casefold(), match["path"]))
    return matches, sorted(reasons), examined


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
))
def search_files(path: str, pattern: str, max_depth: int = 5,
                 max_entries: int = 4096, max_results: int = 50) -> CallToolResult:
    """Find file basenames by case-insensitive glob under an absolute read root.

    Searches names only, without following descendant links/junctions or reading
    contents. Depth 0 examines immediate files. Depth is capped at 16, entries
    at 10000, results at 200, traversal at 5 seconds and match metadata at 32 KiB.
    Partial results disclose the limit; no ambient working directory is inferred.
    """
    try:
        if not isinstance(path, str) or not path.strip() or "\0" in path or not Path(path).is_absolute():
            raise FilesystemPermissionError("invalid_arguments", "Supply an absolute search directory.")
        if (not isinstance(pattern, str) or not pattern or len(pattern) > 256
                or any(char in pattern for char in '/\\') or any(ord(char) < 32 for char in pattern)):
            raise FilesystemPermissionError("invalid_arguments", "Supply a filename glob of at most 256 characters, without directory separators.")
        for value, lower, upper in ((max_depth, 0, 16), (max_entries, 1, 10000), (max_results, 1, 200)):
            if type(value) is not int or not lower <= value <= upper:
                raise FilesystemPermissionError("invalid_arguments", "Search limits must be integers within the documented bounds.")
        config = load_config()
        check_if_path_is_allowed(config, path)
        check_if_path_is_blocked(config, path)
        root = get_full_path(path)
        if not root.is_dir():
            if not root.exists():
                raise FileNotFoundError
            raise NotADirectoryError
        matches, reasons, examined = _search_tree(root, pattern, config, max_depth, max_entries, max_results)
    except FilesystemPermissionError as error:
        return error_result(error.code, str(error))
    except FileNotFoundError:
        return error_result("not_found", "The search directory does not exist.")
    except NotADirectoryError:
        return error_result("not_a_directory", "The search path is not a directory.")
    except PermissionError:
        return error_result("permission_denied", "The search directory could not be inspected.")
    except (OSError, RuntimeError, ValueError):
        return error_result("os_error", "The filesystem search could not complete.")
    text = f"Returned {len(matches)} matching filenames. Only names were searched."
    if reasons:
        text += " This is a partial search: " + ", ".join(reasons) + "."
    if matches:
        text += "\n" + "\n".join(match["path"] for match in matches)
    return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent={
        "kind": "filesystem_search", "root": str(root), "pattern": pattern, "matches": matches,
        "partial": bool(reasons), "truncated": bool(reasons), "limits_reached": reasons,
        "entries_examined": examined,
    })


def _content_bytes(path, root, config, budget):
    """Check candidates again before opening; refuse non-regular or replaced files."""
    check_if_path_is_allowed(config, str(path))
    check_if_path_is_blocked(config, str(path))
    if not path.resolve().is_relative_to(root):
        raise FilesystemPermissionError('outside_allowed_roots', 'The search candidate left its root.')
    for ancestor in (path, *path.parents):
        metadata = ancestor.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise FilesystemPermissionError('unsafe_path', 'A search candidate became a link or reparse point.')
        if ancestor == root:
            break
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        return None, 'non_regular_file'
    if before.st_size > budget:
        return None, 'file_byte_limit'
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            return None, 'changed_file'
        data = source.read(budget + 1)
    return (data, 'file_byte_limit') if len(data) > budget else (data, None)


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
))
def search_content(path: str, query: str, pattern: str = '*', case_sensitive: bool = False,
                   max_depth: int = 5, max_entries: int = 4096, max_files: int = 128,
                   max_results: int = 50, max_file_bytes: int = 65536,
                   max_total_bytes: int = 1048576) -> CallToolResult:
    """Search literal single-line text in authorised UTF-8 files, never shell/regex.

    Return one matching snippet per line with 1-based line/column. Filename
    traversal is bounded independently; at most 200 candidate paths are retained.
    Skip oversized, NUL-containing and non-UTF-8 files and disclose incompleteness.
    Per-file bytes cap at 1 MiB, total at 8 MiB, matches at 200, snippets at 240
    characters, output at 32 KiB and the whole operation at five cooperative seconds.
    """
    try:
        if (not isinstance(query, str) or not query.strip() or len(query) > 256
                or any(ord(char) < 32 for char in query) or type(case_sensitive) is not bool):
            raise FilesystemPermissionError('invalid_arguments', 'Supply literal single-line text of 1 to 256 characters and a boolean case_sensitive flag.')
        try:
            query.encode('utf-8')
        except UnicodeError as error:
            raise FilesystemPermissionError('invalid_arguments', 'The query must be valid UTF-8 text.') from error
        for value, upper in ((max_files, 200), (max_results, 200),
                             (max_file_bytes, MAX_READ_BYTES), (max_total_bytes, 8 * MAX_READ_BYTES)):
            if type(value) is not int or not 1 <= value <= upper:
                raise FilesystemPermissionError('invalid_arguments', 'Content search limits must be integers within the documented bounds.')
        deadline = monotonic() + SEARCH_SECONDS
        config = load_config()
        names = search_files(path, pattern, max_depth=max_depth, max_entries=max_entries, max_results=200)
        if names.is_error:
            return names
        listing = dict(names.structured_content or {})
        if not listing:
            raise FilesystemPermissionError('invalid_result', 'Filename traversal returned no structured observations.')
        root = Path(listing['root'])
        reasons = {'traversal_' + reason for reason in listing['limits_reached']}
        matches, scanned, read_bytes, output_chars = [], 0, 0, 0
        expression = re.compile(re.escape(query), 0 if case_sensitive else re.IGNORECASE)
        for candidate in listing['matches']:
            _search_policy(config, root)
            if monotonic() >= deadline:
                reasons.add('time_limit')
                break
            if scanned >= max_files:
                reasons.add('file_limit')
                break
            remaining = max_total_bytes - read_bytes
            if remaining <= 0:
                reasons.add('total_byte_limit')
                break
            target = Path(candidate['path'])
            scanned += 1
            try:
                data, reason = _content_bytes(target, root, config, min(max_file_bytes, remaining))
                if data is not None:
                    read_bytes += len(data)
                if reason:
                    reasons.add('total_byte_limit' if reason == 'file_byte_limit' and remaining < max_file_bytes else reason)
                    continue
                if b'\0' in data:
                    reasons.add('binary_file')
                    continue
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                reasons.add('invalid_encoding')
                continue
            except FilesystemPermissionError:
                reasons.add('unavailable_file')
                continue
            except (OSError, RuntimeError):
                reasons.add('unavailable_file')
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if monotonic() >= deadline:
                    reasons.add('time_limit')
                    break
                found = expression.search(line)
                if found is None:
                    continue
                start = max(0, found.start() - 80)
                snippet = line[start:start + 240]
                match = {'path': str(target), 'name': target.name, 'line': line_number,
                         'column': found.start() + 1, 'snippet': snippet,
                         'snippet_start_column': start + 1, 'snippet_truncated': start > 0 or start + len(snippet) < len(line)}
                cost = len(json.dumps(match, ensure_ascii=True))
                if output_chars + cost > SEARCH_OUTPUT_CHARS:
                    reasons.add('output_limit')
                    break
                matches.append(match)
                output_chars += cost
                if len(matches) >= max_results:
                    reasons.add('result_limit')
                    break
            if reasons.intersection({'time_limit', 'output_limit', 'result_limit'}):
                break
        _search_policy(config, root)
    except FilesystemPermissionError as error:
        return error_result(error.code, str(error))
    except (OSError, ValueError, RuntimeError, TypeError):
        return error_result('os_error', 'The content search could not complete.')
    text = f'Returned {len(matches)} matching lines from {scanned} examined files.'
    if reasons:
        text += ' This is an incomplete content search: ' + ', '.join(sorted(reasons)) + '.'
    if matches:
        text += '\n' + '\n'.join(f'{match["path"]}:{match["line"]}:{match["column"]}: {match["snippet"]}' for match in matches)
    return CallToolResult(content=[TextContent(type='text', text=text)], structuredContent={
        'kind': 'filesystem_content_search', 'root': str(root), 'query': query, 'pattern': pattern,
        'matches': matches, 'files_examined': scanned, 'bytes_read': read_bytes,
        'entries_examined': listing['entries_examined'], 'partial': bool(reasons),
        'truncated': bool(reasons), 'limits_reached': sorted(reasons),
    })


def _windows_security(source, destination):
    """Apply the original owner/group/DACL before writing any staged content."""
    import ctypes
    from ctypes import wintypes
    security = getattr(ctypes, 'WinDLL')('advapi32', use_last_error=True)
    get_security = security.GetFileSecurityW
    get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_security.restype = wintypes.BOOL
    def descriptor_for(path):
        needed = wintypes.DWORD()
        get_security(str(path), 7, None, 0, ctypes.byref(needed))
        if not 0 < needed.value <= 65536:
            raise OSError('The file security descriptor is unavailable or too large.')
        descriptor = ctypes.create_string_buffer(needed.value)
        if not get_security(str(path), 7, descriptor, needed.value, ctypes.byref(needed)):
            raise getattr(ctypes, 'WinError')(ctypes.get_last_error())
        return descriptor

    descriptor, staged_descriptor = descriptor_for(source), descriptor_for(destination)
    equal_sid = security.EqualSid
    equal_sid.argtypes = [wintypes.LPVOID, wintypes.LPVOID]
    equal_sid.restype = wintypes.BOOL
    flags = 4 | 0x80000000  # Copy and protect the DACL before writing content.
    for function, flag in ((security.GetSecurityDescriptorOwner, 1),
                           (security.GetSecurityDescriptorGroup, 2)):
        function.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
        function.restype = wintypes.BOOL
        original_sid, staged_sid = wintypes.LPVOID(), wintypes.LPVOID()
        defaulted = wintypes.BOOL()
        if not function(descriptor, ctypes.byref(original_sid), ctypes.byref(defaulted)) or not function(
                staged_descriptor, ctypes.byref(staged_sid), ctypes.byref(defaulted)):
            raise getattr(ctypes, 'WinError')(ctypes.get_last_error())
        if not original_sid or not staged_sid:
            raise OSError('The file ownership descriptor is unavailable.')
        # Modify access and file ownership allow safe editing, but do not
        # imply WRITE_OWNER. Request that right only for a real SID change.
        if not equal_sid(original_sid, staged_sid):
            flags |= flag
    set_security = security.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    set_security.restype = wintypes.BOOL
    # Protect the copied DACL from additional parent-directory inheritance.
    if not set_security(str(destination), flags, descriptor):
        raise getattr(ctypes, 'WinError')(ctypes.get_last_error())


def _stage_security(source, destination, descriptor, original):
    if os.name == 'nt':
        _windows_security(source, destination)
        return
    current = os.fstat(descriptor)
    if (current.st_uid, current.st_gid) != (original.st_uid, original.st_gid):
        getattr(os, 'fchown')(descriptor, original.st_uid, original.st_gid)
    # Include POSIX ACLs and security attributes; never silently drop an ACL.
    if hasattr(os, 'listxattr'):
        attributes = os.listxattr(source)
        if 'system.posix_acl_access' not in attributes and 'system.posix_acl_access' in os.listxattr(destination):
            os.removexattr(destination, 'system.posix_acl_access')
        getattr(os, 'fchmod')(descriptor, stat.S_IMODE(original.st_mode))
        total = 0
        for attribute in attributes:
            value = os.getxattr(source, attribute)
            total += len(value)
            if total > 65536:
                raise OSError('File security attributes exceed the supported metadata limit.')
            os.setxattr(destination, attribute, value)
    else:
        getattr(os, 'fchmod')(descriptor, stat.S_IMODE(original.st_mode))


def _replace_text(target, staged, backup):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        replace_file = getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True).ReplaceFileW
        replace_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
        replace_file.restype = wintypes.BOOL
        # Do not ignore ACL/attribute errors. Retain an original-file backup even
        # for Windows' documented partial replacement failure modes.
        if not replace_file(str(target), str(staged), str(backup), 0, None, None):
            raise getattr(ctypes, 'WinError')(ctypes.get_last_error())
    else:
        os.link(target, backup, follow_symlinks=False)
        os.replace(staged, target)


def _mutation_path(path):
    _protect_transaction_paths(_concrete_path(path))
    target = creation_path(path, allow_existing=True)
    config = load_config()
    check_if_path_is_allowed(config, str(target))
    check_if_path_is_blocked(config, str(target))
    metadata = target.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise FilesystemPermissionError('unsafe_path', 'Choose a regular file with no hard-link aliases.')
    if not metadata.st_mode & 0o222 or metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise FilesystemPermissionError('permission_denied', 'Read-only or privileged files cannot be edited.')
    return target, metadata, config


def _text_mutation(path, expected_sha256, operation, *, old_text=None, content=''):
    artifacts = []
    attempted = False
    committed = False
    try:
        if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-fA-F]{64}', expected_sha256):
            raise FilesystemPermissionError('invalid_arguments', 'Supply the SHA-256 from a complete revision read.')
        if not isinstance(content, str) or (operation == 'edit' and (not isinstance(old_text, str) or not old_text)):
            raise FilesystemPermissionError('invalid_arguments', 'Supply nonempty old_text for an edit and UTF-8 replacement or append text.')
        if (len(content.encode('utf-8')) > MAX_READ_BYTES
                or (operation == 'edit' and len(old_text.encode('utf-8')) > MAX_READ_BYTES)):
            raise FilesystemPermissionError('content_too_large', 'Mutation text is limited to 1 MiB of UTF-8.')
        target, before, config = _mutation_path(path)
        if before.st_size > MAX_READ_BYTES:
            raise FilesystemPermissionError('content_too_large', 'The existing file exceeds the 1 MiB mutation limit.')
        parent = target.parent
        token = uuid.uuid4().hex
        staged = creation_path(str(parent / ('.coda-edit-' + token + '.tmp')))
        backup = creation_path(str(parent / ('.coda-backup-' + token)))
        # A stable folder lock survives target replacement and filename aliases.
        # Mutations in the same folder fail busy rather than racing each other.
        lock_name = '.coda-lock'
        lock = creation_path(str(parent / lock_name), allow_existing=True)
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise FilesystemPermissionError('mutation_in_progress', 'Another mutation or interrupted transaction owns this file lock.') from error
        artifacts.append(lock)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(json.dumps({'target': str(target), 'staged': str(staged), 'backup': str(backup),
                                     'expected_sha256': expected_sha256.lower()}).encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())
        original = _read_safe_bytes(target)
        if hashlib.sha256(original).hexdigest() != expected_sha256.lower():
            raise FilesystemPermissionError('content_changed', 'The file has changed. Read its current revision before requesting another mutation.')
        text = original.decode('utf-8')
        if '\0' in text:
            raise FilesystemPermissionError('invalid_encoding', 'Binary files cannot be edited as text.')
        if operation == 'edit':
            occurrences = text.count(old_text)
            if occurrences != 1:
                raise FilesystemPermissionError('ambiguous_edit', 'The old text must occur exactly once. Nothing was changed.')
            changed = text.replace(old_text, content, 1).encode('utf-8')
        else:
            changed = original + content.encode('utf-8')
        if len(changed) > MAX_READ_BYTES:
            raise FilesystemPermissionError('content_too_large', 'The resulting file must fit within 1 MiB of UTF-8.')
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        artifacts.append(staged)
        with os.fdopen(descriptor, 'wb') as output:
            _stage_security(target, staged, output.fileno(), before)
            output.write(changed)
            output.flush()
            os.fsync(output.fileno())
        # Check both permission planes and source identity again immediately
        # before replacement. Cooperative writers are excluded by the lock.
        _, current, fresh = _mutation_path(path)
        if fresh != config or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise FilesystemPermissionError('content_changed', 'The file or its permissions changed before replacement.')
        if os.name != 'nt' and (current.st_mode, current.st_uid, current.st_gid, current.st_ctime_ns) != (
                before.st_mode, before.st_uid, before.st_gid, before.st_ctime_ns):
            raise FilesystemPermissionError('content_changed', 'File metadata changed before replacement.')
        if hashlib.sha256(_read_safe_bytes(target)).hexdigest() != expected_sha256.lower():
            raise FilesystemPermissionError('content_changed', 'The file changed before replacement.')
        creation_path(str(backup))
        artifacts.append(backup)
        attempted = True
        _replace_text(target, staged, backup)
        if _read_safe_bytes(target) != changed:
            raise FilesystemPermissionError('mutation_incomplete', 'The replacement could not be verified.')
        committed = True
    except (FilesystemPermissionError, OSError, ValueError, UnicodeError, RuntimeError) as error:
        if isinstance(error, FilesystemPermissionError):
            code = error.code
        elif isinstance(error, UnicodeError):
            code = 'invalid_encoding'
        elif isinstance(error, FileNotFoundError):
            code = 'not_found'
        elif isinstance(error, PermissionError):
            code = 'permission_denied'
        elif isinstance(error, ValueError):
            code = 'content_too_large'
        else:
            code = 'os_error'
        if attempted:
            code = 'mutation_incomplete'
        result = error_result(code, str(error))
    else:
        result = CallToolResult(content=[TextContent(type='text', text=f'{operation.capitalize()} completed: {target}')],
            structuredContent={'kind': 'filesystem_changed', 'path': str(target), 'operation': operation,
                               'sha256': hashlib.sha256(changed).hexdigest(), 'size_bytes': len(changed)})
    # Before replacement the original is intact, so temporary files can go.
    # After a failed replacement retain all recovery evidence and the lock.
    pending = []
    if not attempted or committed:
        for artifact in reversed(artifacts):
            try:
                artifact.unlink(missing_ok=True)
            except OSError:
                pending.append(str(artifact))
    else:
        pending = [str(artifact) for artifact in artifacts if artifact.exists()]
    if pending:
        payload = dict(result.structured_content or {})
        payload['recovery_paths'] = pending
        payload['target_may_have_changed'] = attempted
        result.content.append(TextContent(type='text', text='Recovery files retained:\n' + '\n'.join(pending)
                                          + '\nInspect the target and recovery files before another mutation.'))
        result = CallToolResult(isError=result.is_error, content=result.content, structuredContent=payload)
    return result


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False,
))
def edit_file(path: str, old_text: str, new_text: str, expected_sha256: str) -> CallToolResult:
    """Replace exactly one literal occurrence in a bounded UTF-8 file.

    Requires independent read/write grants, host confirmation and the SHA-256
    from read_file(include_revision=true). Reject conflicts and ambiguous edits.
    Stage in the same directory, preserve file security and retain recovery
    artifacts if replacement fails. Never automatically replay a failed edit.
    """
    return _text_mutation(path, expected_sha256, 'edit', old_text=old_text, content=new_text)


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False,
))
def append_file(path: str, content: str, expected_sha256: str) -> CallToolResult:
    """Append exact UTF-8 text to an existing bounded file using revision checks.

    No newline is inserted automatically. Uses the same grants, confirmation,
    staged replacement, conflict detection and recovery policy as edit_file.
    """
    return _text_mutation(path, expected_sha256, 'append', content=content)


def _rename_no_replace(source, destination):
    """Publish without an existence-check/overwrite race on supported platforms."""
    if os.name == 'nt':
        os.rename(source, destination)
        return
    if sys.platform != 'linux':
        raise FilesystemPermissionError('unsupported_operation', 'No safe no-replace rename is available on this platform.')
    import ctypes
    import errno
    rename = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
    if rename is None:
        raise FilesystemPermissionError('unsupported_operation', 'The C library has no renameat2 support.')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP):
            raise FilesystemPermissionError('unsupported_operation', 'The filesystem has no safe no-replace rename support.')
        raise OSError(code, os.strerror(code), str(destination))


def _copy_source(path, config):
    try:
        check_if_path_is_allowed(config, str(path))
        check_if_path_is_blocked(config, str(path))
    except FilesystemPermissionError as error:
        raise FilesystemPermissionError(error.code, 'The copy source or a descendant is outside its read authority or blocked.') from error
    except RuntimeError as error:
        raise FilesystemPermissionError('unsafe_path', 'A copy source could not be safely resolved.') from error
    for ancestor in (path, *path.parents):
        metadata = ancestor.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise FilesystemPermissionError('unsafe_path', 'Copying through links, junctions or reparse points is not supported.')
    if (path.parent / '.coda-lock').exists() or (path.is_dir() and (path / '.coda-lock').exists()):
        raise FilesystemPermissionError('mutation_in_progress', 'Resolve the active or interrupted source-folder transaction first.')
    metadata = path.lstat()
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise FilesystemPermissionError('unsafe_path', 'Copy only ordinary files or directories.')
    return metadata


def _copy_signature(metadata, *, opened_identity=False):
    # Windows CPython 3.12+ stat may expose creation time as ctime while fstat
    # exposes metadata-change time (CPython #157671). Compare birthtime across
    # APIs where available, and keep full ctime for comparisons within one API.
    timestamp = getattr(metadata, 'st_birthtime_ns', metadata.st_ctime_ns) if opened_identity else metadata.st_ctime_ns
    return metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size, metadata.st_mtime_ns, timestamp


def _copy_guard(config, source, destination, deadline):
    if monotonic() >= deadline:
        raise FilesystemPermissionError('resource_limit', 'The copy reached its five-second cooperative time limit.')
    if load_config() != config:
        raise FilesystemPermissionError('permission_denied', 'Filesystem configuration changed during the copy.')
    _copy_source(source, config)
    creation_path(str(destination))


def _copy_manifest(source, destination, config, max_entries, max_bytes, max_depth, deadline):
    pending = deque([(source, Path('.'), 0)])
    entries, size, path_chars = [], 0, 0
    while pending:
        current, relative, depth = pending.popleft()
        _copy_guard(config, source, destination, deadline)
        metadata = _copy_source(current, config)
        creation_path(str(destination / relative))
        is_directory = stat.S_ISDIR(metadata.st_mode)
        if is_directory and depth > max_depth:
            raise FilesystemPermissionError('resource_limit', 'The source tree exceeds the depth limit.')
        entries.append((relative, is_directory, metadata))
        path_chars += len(str(relative))
        if len(entries) > max_entries or path_chars > 65536:
            raise FilesystemPermissionError('resource_limit', 'The source tree exceeds its entry or path-metadata limit.')
        if not is_directory:
            size += metadata.st_size
            if size > max_bytes:
                raise FilesystemPermissionError('resource_limit', 'The source tree exceeds the total copy byte limit.')
            continue
        children = []
        with os.scandir(current) as directory:
            for entry in directory:
                if len(entries) + len(pending) + len(children) >= max_entries:
                    raise FilesystemPermissionError('resource_limit', 'The source tree exceeds its entry limit.')
                if monotonic() >= deadline:
                    raise FilesystemPermissionError('resource_limit', 'The copy reached its time limit during enumeration.')
                children.append(entry.name)
        for name in sorted(children, key=lambda name: (name.casefold(), name)):
            _concrete_path(str(current / name))
            pending.append((current / name, relative / name, depth + 1))
    return entries


def _copy_file_data(source, staged, output, metadata, remaining, config, root, destination, deadline):
    _copy_guard(config, root, destination, deadline)
    current = _copy_source(source, config)
    if _copy_signature(current) != _copy_signature(metadata):
        raise FilesystemPermissionError('content_changed', 'A source file changed before copying.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(source, flags)
    copied = 0
    with os.fdopen(descriptor, 'rb') as input_file:
        opened = os.fstat(input_file.fileno())
        if _copy_signature(opened, opened_identity=True) != _copy_signature(metadata, opened_identity=True):
            raise FilesystemPermissionError('content_changed', 'The opened source file changed identity.')
        while True:
            _copy_guard(config, root, destination, deadline)
            _copy_source(source, config)
            creation_path(str(staged), allow_existing=True)
            data = input_file.read(min(65536, remaining - copied + 1))
            if not data:
                break
            if copied + len(data) > remaining:
                raise FilesystemPermissionError('resource_limit', 'A growing file exhausted the copy byte budget.')
            output.write(data)
            copied += len(data)
        output.flush()
        os.fsync(output.fileno())
        if _copy_signature(os.fstat(input_file.fileno())) != _copy_signature(opened):
            raise FilesystemPermissionError('content_changed', 'A source file changed while copying.')
    return copied


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
))
def copy_path(source: str, destination: str, max_entries: int = 1024,
              max_bytes: int = 33554432, max_depth: int = 8) -> CallToolResult:
    """Copy file data or a directory tree to a new absolute destination, never overwrite.

    Independently requires read authority for every source and write authority
    for every destination/staging entry, plus CODA confirmation. No links or
    blocked descendants are copied. Preflight caps entries (4096 maximum),
    bytes (128 MiB maximum), depth (16 maximum), paths (64 KiB), and the whole
    operation at five cooperative seconds. Interrupted staging is retained.
    File security/ownership metadata is not cloned to the approved destination.
    """
    staged = None
    staged_created = False
    publication_attempted = False
    try:
        deadline = monotonic() + SEARCH_SECONDS
        for value, lower, upper in ((max_entries, 1, 4096), (max_bytes, 1, 134217728), (max_depth, 0, 16)):
            if type(value) is not int or not lower <= value <= upper:
                raise FilesystemPermissionError('invalid_arguments', 'Supply copy limits within the documented integer bounds.')
        source_path = _concrete_path(source)
        config = load_config()
        source_metadata = _copy_source(source_path, config)
        source_path = source_path.resolve()
        target = creation_path(destination)
        _protect_transaction_paths(target)
        if not target.parent.is_dir():
            raise FilesystemPermissionError('not_found', 'The destination parent must already exist.')
        if stat.S_ISDIR(source_metadata.st_mode):
            for ancestor in (target.parent, *target.parent.parents):
                if monotonic() >= deadline:
                    raise FilesystemPermissionError('resource_limit', 'Copy path checks reached their time limit.')
                metadata = ancestor.stat()
                if (metadata.st_dev, metadata.st_ino) == (source_metadata.st_dev, source_metadata.st_ino):
                    raise FilesystemPermissionError('invalid_arguments', 'A directory cannot be copied into itself.')
        entries = _copy_manifest(source_path, target, config, max_entries, max_bytes, max_depth, deadline)
        if _copy_signature(entries[0][2]) != _copy_signature(source_metadata):
            raise FilesystemPermissionError('content_changed', 'The source changed during copy preflight.')
        staged = creation_path(str(target.parent / ('.coda-copy-' + uuid.uuid4().hex)))
        copied = 0
        staged_entries = []
        for relative, is_directory, metadata in entries:
            _copy_guard(config, source_path, target, deadline)
            output_path = staged / relative if stat.S_ISDIR(source_metadata.st_mode) else staged
            creation_path(str(output_path))
            if is_directory:
                output_path.mkdir(mode=0o700)
                staged_created = True
                created = output_path.lstat()
                staged_entries.append((output_path, True, (created.st_dev, created.st_ino)))
            else:
                with output_path.open('xb') as output:
                    staged_created = True
                    copied += _copy_file_data(source_path / relative, output_path, output, metadata,
                                             max_bytes - copied, config, source_path, target, deadline)
                    opened = os.fstat(output.fileno())
                created = output_path.lstat()
                if (created.st_dev, created.st_ino) != (opened.st_dev, opened.st_ino):
                    raise FilesystemPermissionError('content_changed', 'A staged file changed identity.')
                staged_entries.append((output_path, False, _copy_signature(created)))
        for relative, _, metadata in entries:
            _copy_guard(config, source_path, target, deadline)
            if _copy_signature(_copy_source(source_path / relative, config)) != _copy_signature(metadata):
                raise FilesystemPermissionError('content_changed', 'The source tree changed before publication.')
        for output_path, is_directory, expected in staged_entries:
            creation_path(str(output_path), allow_existing=True)
            current = output_path.lstat()
            actual = (current.st_dev, current.st_ino) if is_directory else _copy_signature(current)
            if actual != expected:
                raise FilesystemPermissionError('content_changed', 'Staged copy data changed before publication.')
        _copy_guard(config, source_path, target, deadline)
        publication_attempted = True
        _rename_no_replace(staged, target)
    except (FilesystemPermissionError, OSError, ValueError, RuntimeError) as error:
        if isinstance(error, FilesystemPermissionError):
            code = error.code
        elif isinstance(error, FileExistsError):
            code = 'already_exists'
        elif isinstance(error, FileNotFoundError):
            code = 'not_found'
        elif isinstance(error, PermissionError):
            code = 'permission_denied'
        else:
            code = 'os_error'
        if not staged_created:
            return error_result(code, str(error))
        return CallToolResult(isError=True, content=[TextContent(type='text', text=
            f'Copy did not complete: {error}. Retained staging: {staged}. Inspect the destination before retrying.')],
            structuredContent={'error': {'code': 'copy_incomplete', 'message': str(error)},
                               'cause_code': code, 'staging_path': str(staged),
                               'destination': destination, 'publication_attempted': publication_attempted})
    return CallToolResult(content=[TextContent(type='text', text=f'Copied {source_path} to {target}; {copied} bytes.')],
        structuredContent={'kind': 'filesystem_copied', 'source': str(source_path), 'destination': str(target),
                           'bytes_copied': copied, 'entries_copied': len(entries),
                           'type': 'directory' if stat.S_ISDIR(source_metadata.st_mode) else 'file'})


def _protect_operator_paths(target, *, contains=False):
    """File tools cannot rewrite their own authority or running installation."""
    target = target.resolve()
    server_root = Path(__file__).resolve().parent
    roots = [server_root, *OPERATOR_PATHS]
    installation = server_root.parent.parent
    if (installation / 'coda_runtime.py').is_file() and (installation / 'ai/mcp/runtime.py').is_file():
        roots.append(installation)
    protected_file = CONFIG_PATH.resolve()
    if target == protected_file or (contains and protected_file.is_relative_to(target)):
        raise FilesystemPermissionError('protected_path', 'Operator permissions cannot be changed by filesystem tools.')
    for root in roots:
        if target.is_relative_to(root) or (contains and root.is_relative_to(target)):
            raise FilesystemPermissionError('protected_path', 'The running integration/application is protected from filesystem mutations.')
    # Physical aliases (for example bind mounts) need not resolve to the same
    # spelling. Match existing target/ancestor identities as well as paths.
    protected_identities = set()
    for root in roots:
        try:
            metadata = root.stat()
        except FileNotFoundError:
            continue
        protected_identities.add((metadata.st_dev, metadata.st_ino))
    try:
        metadata = protected_file.stat()
        configuration_identity = (metadata.st_dev, metadata.st_ino)
    except FileNotFoundError:
        configuration_identity = None
    for ancestor in (target, *target.parents):
        try:
            metadata = ancestor.stat()
        except FileNotFoundError:
            continue
        identity = metadata.st_dev, metadata.st_ino
        if identity in protected_identities or (ancestor == target and identity == configuration_identity):
            raise FilesystemPermissionError('protected_path', 'An operator/runtime path alias is protected from mutations.')


def _protect_transaction_paths(target):
    if any(re.fullmatch(r'\.coda-(?:lock|edit-[0-9a-f]{32}\.tmp|backup-[0-9a-f]{32}|copy-[0-9a-f]{32})', ancestor.name.casefold())
           for ancestor in (target, *target.parents)):
        raise FilesystemPermissionError('protected_path', 'Transaction recovery artifacts require explicit operator inspection.')


def _destructive_path(path, config, owned_locks=()):
    target = _concrete_path(str(path))
    destructive = config.get('destructive', {})
    if not isinstance(destructive, dict) or type(destructive.get('enabled', False)) is not bool:
        raise FilesystemPermissionError('configuration_error', 'Destructive configuration is invalid.')
    if not destructive.get('enabled', False):
        raise FilesystemPermissionError('destructive_disabled', 'Destructive filesystem authority is disabled.')
    roots = destructive.get('allowed_roots', [])
    blocked = destructive.get('blocked_paths', [])
    if (not isinstance(roots, list) or len(roots) > 64
            or any(not isinstance(root, str) or not Path(root).is_absolute() or '\0' in root for root in roots)
            or not isinstance(blocked, list) or any(not isinstance(pattern, str) or not pattern.strip() for pattern in blocked)
            or type(destructive.get('require_confirmation', True)) is not bool):
        raise FilesystemPermissionError('configuration_error', 'Destructive roots or blocks are invalid.')
    policy = {'read': {'allowed_roots': roots, 'blocked_paths': config['read']['blocked_paths'] + blocked}}
    try:
        check_if_path_is_allowed(policy, str(target))
        check_if_path_is_blocked(policy, str(target))
    except FilesystemPermissionError as error:
        raise FilesystemPermissionError(error.code, 'The destructive target or a descendant is outside its authority or blocked.') from error
    _protect_operator_paths(target, contains=True)
    for ancestor in (target, *target.parents):
        metadata = ancestor.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise FilesystemPermissionError('unsafe_path', 'Destructive operations refuse linked/reparse ancestors or targets.')
    for lock in (target.parent / '.coda-lock', target / '.coda-lock' if target.is_dir() else target.parent / '.coda-lock'):
        if lock.exists() and lock not in owned_locks:
            raise FilesystemPermissionError('mutation_in_progress', 'Inspect the interrupted or active transaction before destructive work.')
    _protect_transaction_paths(target)
    metadata = target.lstat()
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)) or (
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1):
        raise FilesystemPermissionError('unsafe_path', 'Choose an ordinary entry without aliases.')
    protected = [Path(target.anchor)]
    write = config.get('write', {})
    if not isinstance(write, dict):
        raise FilesystemPermissionError('configuration_error', 'Write boundaries are invalid.')
    for group in (config['read']['allowed_roots'], config['read'].get('workspace_roots', []),
                  write.get('allowed_roots', []), roots):
        if not isinstance(group, list) or len(group) > 64 or any(
                not isinstance(root, str) or not Path(root).is_absolute() or '\0' in root for root in group):
            raise FilesystemPermissionError('configuration_error', 'Protected filesystem boundaries are invalid.')
        protected.extend(Path(root).resolve() for root in group)
    canonical = target.resolve()
    for root in protected:
        if canonical == root or root.is_relative_to(canonical):
            raise FilesystemPermissionError('protected_path', 'Configured roots and workspaces cannot be removed or moved.')
        try:
            root_metadata = root.stat()
        except FileNotFoundError:
            continue
        if (root_metadata.st_dev, root_metadata.st_ino) == (metadata.st_dev, metadata.st_ino):
            raise FilesystemPermissionError('protected_path', 'A configured root alias is protected.')
    return target, metadata


def _destructive_manifest(root, config, recursive, max_entries, max_depth, deadline):
    pending = deque([(root, 0)])
    entries = []
    path_chars = 0
    while pending:
        if monotonic() >= deadline:
            raise FilesystemPermissionError('resource_limit', 'Destructive traversal reached its time limit.')
        if load_config() != config:
            raise FilesystemPermissionError('permission_denied', 'Permissions changed during destructive preflight.')
        current, depth = pending.popleft()
        target, metadata = _destructive_path(current, config)
        entries.append((target, metadata))
        path_chars += len(str(target.relative_to(root)))
        if len(entries) > max_entries or path_chars > 65536:
            raise FilesystemPermissionError('resource_limit', 'Destructive scope exceeds its entry/path limit.')
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        if depth > max_depth:
            raise FilesystemPermissionError('resource_limit', 'Destructive scope exceeds its directory-depth limit.')
        children = []
        with os.scandir(target) as directory:
            for entry in directory:
                if not recursive:
                    raise FilesystemPermissionError('directory_not_empty', 'Set recursive=true explicitly to delete a nonempty directory.')
                if monotonic() >= deadline or len(entries) + len(pending) + len(children) >= max_entries:
                    raise FilesystemPermissionError('resource_limit', 'Destructive scope reached its time or entry limit.')
                children.append(entry.name)
        for name in sorted(children, key=lambda name: (name.casefold(), name)):
            pending.append((target / name, depth + 1))
    return entries


def _release_operation_lock(path, owned_locks):
    expected = owned_locks.get(path)
    if expected is None:
        return
    current = path.lstat()
    if (current.st_dev, current.st_ino) != expected or stat.S_ISLNK(current.st_mode):
        raise FilesystemPermissionError('content_changed', 'An operation lock changed identity; it was not removed.')
    path.unlink()
    del owned_locks[path]


def _lock_destructive_scope(root, entries, config, deadline, owned_locks, operation='delete'):
    """Internal metadata locks belong to the approved destructive transaction."""
    directories = [root.parent] + [path for path, metadata in entries if stat.S_ISDIR(metadata.st_mode)]
    authority = config['destructive']
    policy = {'read': {'allowed_roots': authority['allowed_roots'],
                       'blocked_paths': config['read']['blocked_paths'] + authority.get('blocked_paths', [])}}
    transaction = uuid.uuid4().hex
    for index, directory in enumerate(directories):
        if monotonic() >= deadline:
            raise FilesystemPermissionError('resource_limit', 'Destructive lock acquisition reached its time limit.')
        if load_config() != config:
            raise FilesystemPermissionError('permission_denied', 'Permissions changed while acquiring destructive locks.')
        lock = directory / '.coda-lock'
        check_if_path_is_allowed(policy, str(lock))
        check_if_path_is_blocked(policy, str(lock))
        _protect_operator_paths(lock)
        for ancestor in (directory, *directory.parents):
            metadata = ancestor.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                raise FilesystemPermissionError('unsafe_path', 'A lock directory became a link or reparse point.')
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise FilesystemPermissionError('mutation_in_progress', 'Another transaction owns a required folder lock.') from error
        with os.fdopen(descriptor, 'wb') as output:
            created = os.fstat(output.fileno())
            owned_locks[lock] = (created.st_dev, created.st_ino)
            record = {'operation': operation, 'transaction': transaction, 'outcome': 'unverified'}
            record['target' if index == 0 else 'relative_folder'] = str(root if index == 0 else directory.relative_to(root))
            output.write(json.dumps(record, ensure_ascii=False).encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())


def _verify_destructive_entry(current, original, config, owned_locks, deadline):
    if monotonic() >= deadline:
        raise FilesystemPermissionError('resource_limit', 'Deletion reached its time limit.')
    if load_config() != config:
        raise FilesystemPermissionError('permission_denied', 'Permissions changed during deletion.')
    _, fresh = _destructive_path(current, config, owned_locks)
    if stat.S_ISDIR(original.st_mode):
        same = (fresh.st_dev, fresh.st_ino, fresh.st_mode) == (original.st_dev, original.st_ino, original.st_mode)
    else:
        same = _copy_signature(fresh) == _copy_signature(original)
    if not same:
        raise FilesystemPermissionError('content_changed', 'An entry changed before deletion.')


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False,
))
def delete_path(path: str, recursive: bool = False, max_entries: int = 1024, max_depth: int = 8) -> CallToolResult:
    """Delete an authorised file or directory with separate destructive permission.

    CODA always requires action-bound confirmation. Nonempty directories require
    recursive=true; every bounded descendant is authorised before any deletion.
    Protect roots/workspaces, operator configuration, runtime and recovery paths.
    Entries cap at 4096, depth at 16, path metadata at 64 KiB and cooperative time
    at five seconds. Interrupted/partial deletion is never automatically replayed.
    """
    removed_files = removed_directories = 0
    attempted = False
    owned_locks = {}
    try:
        deadline = monotonic() + SEARCH_SECONDS
        if type(recursive) is not bool or type(max_entries) is not int or not 1 <= max_entries <= 4096 or (
                type(max_depth) is not int or not 0 <= max_depth <= 16):
            raise FilesystemPermissionError('invalid_arguments', 'Supply a boolean recursive flag and bounded integer deletion limits.')
        target = _concrete_path(path)
        config = load_config()
        entries = _destructive_manifest(target, config, recursive, max_entries, max_depth, deadline)
        _lock_destructive_scope(target, entries, config, deadline, owned_locks)
        for current, original in entries:
            _verify_destructive_entry(current, original, config, owned_locks, deadline)
        for current, original in reversed(entries):
            _verify_destructive_entry(current, original, config, owned_locks, deadline)
            attempted = True
            if stat.S_ISDIR(original.st_mode):
                _release_operation_lock(current / '.coda-lock', owned_locks)
                current.rmdir()
                removed_directories += 1
            else:
                current.unlink()
                removed_files += 1
    except (FilesystemPermissionError, OSError, ValueError, RuntimeError) as error:
        code = error.code if isinstance(error, FilesystemPermissionError) else (
            'not_found' if isinstance(error, FileNotFoundError) else 'permission_denied' if isinstance(error, PermissionError) else 'os_error')
        if not attempted:
            result = error_result(code, str(error))
        else:
            result = CallToolResult(isError=True, content=[TextContent(type='text', text=
                f'Deletion interrupted: {removed_files} files and {removed_directories} directories confirmed removed. Inspect the remaining target before retrying.')],
                structuredContent={'error': {'code': 'delete_incomplete', 'message': str(error)},
                    'cause_code': code, 'path': path, 'recursive': recursive, 'partial': True,
                    'files_removed': removed_files, 'directories_removed': removed_directories,
                    'outcome_may_be_incomplete': True})
    else:
        result = CallToolResult(content=[TextContent(type='text', text=
            f'Deleted {target}: {removed_files} files and {removed_directories} directories.')], structuredContent={
            'kind': 'filesystem_deleted', 'path': str(target), 'recursive': recursive,
            'files_removed': removed_files, 'directories_removed': removed_directories})
    if not attempted or not result.is_error:
        for lock in list(reversed(owned_locks)):
            try:
                _release_operation_lock(lock, owned_locks)
            except (OSError, FilesystemPermissionError):
                pass
    if owned_locks:
        payload = dict(result.structured_content or {})
        examples = []
        output_chars = 2
        for lock in owned_locks:
            name = str(lock)
            cost = len(json.dumps(name, ensure_ascii=True)) + 2
            if len(examples) >= 20 or output_chars + cost > SEARCH_OUTPUT_CHARS:
                break
            examples.append(name)
            output_chars += cost
        payload['recovery_paths'] = examples
        payload['recovery_lock_count'] = len(owned_locks)
        payload['recovery_paths_truncated'] = len(examples) < len(owned_locks)
        result = CallToolResult(isError=result.is_error, content=result.content + [TextContent(type='text', text=
            f'Retained {len(owned_locks)} transaction locks. Example locations:\n' + '\n'.join(examples)
            + '\nInspect the remaining target before any retry or lock cleanup.')], structuredContent=payload)
    return result


def _move_destination(source, target, entries, config, owned_locks, deadline):
    """Every relocated entry needs destination write authority, including names."""
    expected_children = {}
    for current, metadata in entries:
        _verify_destructive_entry(current, metadata, config, owned_locks, deadline)
        output = target / current.relative_to(source)
        creation_path(str(output))
        _protect_transaction_paths(output)
        if current != source:
            expected_children.setdefault(current.parent, set()).add(current.name)
        if stat.S_ISDIR(metadata.st_mode):
            expected_children.setdefault(current, set())
    # A single rename moves the whole tree. Refuse new descendants rather than
    # silently including names that were never authorised during preflight.
    for directory, expected in expected_children.items():
        actual = set()
        with os.scandir(directory) as children:
            for child in children:
                if monotonic() >= deadline or len(actual) > len(expected):
                    raise FilesystemPermissionError('content_changed', 'The move scope changed before publication.')
                if directory / child.name not in owned_locks:
                    actual.add(child.name)
        if actual != expected:
            raise FilesystemPermissionError('content_changed', 'The move scope changed before publication.')


@server.tool(structured_output=False, annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False,
))
def move_path(source: str, destination: str, max_entries: int = 1024, max_depth: int = 8) -> CallToolResult:
    """Move/rename an authorised file or complete bounded directory to a new path.

    Requires independent destructive source and write destination grants, plus
    CODA confirmation. Never overwrite or copy/delete across filesystems. Parent
    must exist; roots, links, aliases, blocked descendants and recovery paths are
    refused. Limits: 4096 entries, depth 16, 64 KiB paths, five cooperative seconds.
    Cancellation can leave a completed rename without a returned result: inspect
    both paths and retained locks before any new request. Never automatically retry.
    """
    owned_locks = {}
    attempted = False
    relocated = False
    target = source_path = None
    try:
        deadline = monotonic() + SEARCH_SECONDS
        if type(max_entries) is not int or not 1 <= max_entries <= 4096 or (
                type(max_depth) is not int or not 0 <= max_depth <= 16):
            raise FilesystemPermissionError('invalid_arguments', 'Supply bounded integer move limits.')
        source_path = _concrete_path(source)
        config = load_config()
        entries = _destructive_manifest(source_path, config, True, max_entries, max_depth, deadline)
        target = creation_path(destination)
        _protect_transaction_paths(target)
        if not target.parent.is_dir():
            raise FilesystemPermissionError('not_found', 'The destination parent must already exist.')
        source_metadata = entries[0][1]
        for ancestor in (target.parent, *target.parent.parents):
            metadata = ancestor.stat()
            if (metadata.st_dev, metadata.st_ino) == (source_metadata.st_dev, source_metadata.st_ino):
                raise FilesystemPermissionError('invalid_arguments', 'A directory cannot be moved into itself.')
        if source_metadata.st_dev != target.parent.stat().st_dev:
            raise FilesystemPermissionError('cross_device', 'Cross-filesystem moves are unsupported; no copy/delete fallback was attempted.')
        _move_destination(source_path, target, entries, config, owned_locks, deadline)
        _lock_destructive_scope(source_path, entries, config, deadline, owned_locks, operation='move')
        destination_lock = target.parent / '.coda-lock'
        if destination_lock not in owned_locks:
            creation_path(str(destination_lock))
            with destination_lock.open('xb') as output:
                metadata = os.fstat(output.fileno())
                owned_locks[destination_lock] = metadata.st_dev, metadata.st_ino
                output.write(json.dumps({'operation': 'move', 'source': str(source_path),
                                         'destination': str(target), 'outcome': 'unverified'}).encode('utf-8'))
                output.flush()
                os.fsync(output.fileno())
        _move_destination(source_path, target, entries, config, owned_locks, deadline)
        creation_path(str(target))
        if load_config() != config:
            raise FilesystemPermissionError('permission_denied', 'Permissions changed before the move.')
        attempted = True
        _rename_no_replace(source_path, target)
        relocated = True
        owned_locks = {(target / lock.relative_to(source_path) if lock.is_relative_to(source_path) else lock): identity
                       for lock, identity in owned_locks.items()}
        published = target.lstat()
        if (published.st_dev, published.st_ino) != (source_metadata.st_dev, source_metadata.st_ino) or source_path.exists():
            raise FilesystemPermissionError('content_changed', 'The rename outcome could not be verified.')
    except (FilesystemPermissionError, OSError, ValueError, RuntimeError) as error:
        import errno
        code = error.code if isinstance(error, FilesystemPermissionError) else (
            'already_exists' if isinstance(error, FileExistsError) else 'not_found' if isinstance(error, FileNotFoundError)
            else 'permission_denied' if isinstance(error, PermissionError)
            else 'cross_device' if isinstance(error, OSError) and error.errno == errno.EXDEV else 'os_error')
        if attempted:
            result = CallToolResult(isError=True, content=[TextContent(type='text', text=
                'The move outcome is unverified. Inspect both paths and transaction locks before any new request.')],
                structuredContent={'error': {'code': 'move_incomplete', 'message': str(error)},
                    'cause_code': code, 'source': source, 'destination': destination, 'rename_attempted': True})
        else:
            result = error_result(code, str(error))
    else:
        result = CallToolResult(content=[TextContent(type='text', text=f'Moved {source_path} to {target}.')],
            structuredContent={'kind': 'filesystem_moved', 'source': str(source_path), 'destination': str(target),
                'entries_moved': len(entries), 'type': 'directory' if stat.S_ISDIR(source_metadata.st_mode) else 'file'})
    if not attempted or not result.is_error:
        for lock in list(reversed(owned_locks)):
            try:
                _release_operation_lock(lock, owned_locks)
            except (OSError, FilesystemPermissionError):
                pass
    if owned_locks:
        # If rename raised after a remote effect, locks may have moved with the
        # directory. Report both possible roots rather than claiming a location.
        payload = dict(result.structured_content or {})
        examples, output_chars = [], 2
        for lock in owned_locks:
            name = str(lock)
            cost = len(json.dumps(name, ensure_ascii=True)) + 2
            if len(examples) >= 20 or output_chars + cost > SEARCH_OUTPUT_CHARS:
                break
            examples.append(name)
            output_chars += cost
        payload.update(recovery_lock_count=len(owned_locks), recovery_paths=examples,
                       recovery_paths_truncated=len(owned_locks) > len(examples))
        if attempted and not relocated:
            payload['possible_relocated_root'] = str(target)
        result = CallToolResult(isError=result.is_error, content=result.content + [TextContent(type='text', text=
            'Transaction locks remain. Inspect source, destination and structured recovery details before cleanup or retry.')],
            structuredContent=payload)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Explicit filesystem permissions file")
    parser.add_argument('--operator-paths-json', help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.config is not None:
        CONFIG_PATH = options.config.resolve()
    if options.operator_paths_json is not None:
        try:
            if len(options.operator_paths_json) > 16384:
                raise ValueError('Oversized operator protection metadata.')
            protected_paths = json.loads(options.operator_paths_json)
            if not isinstance(protected_paths, list) or len(protected_paths) > 128 or any(
                    not isinstance(path, str) or not path or '\0' in path or not Path(path).is_absolute()
                    for path in protected_paths):
                raise ValueError('Invalid operator protection paths.')
            OPERATOR_PATHS = tuple(Path(path).resolve() for path in protected_paths)
        except (ValueError, TypeError, OSError, RuntimeError) as error:
            parser.error(str(error))
    server.run(transport="stdio")
