"""Standalone filesystem MCP with separate, explicit read and create permissions."""

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
from time import monotonic
from collections import deque


MAX_READ_BYTES = 1024 * 1024
DEFAULT_RANGE_BYTES = 64 * 1024
SEARCH_SECONDS = 5
SEARCH_OUTPUT_CHARS = 32768


# Locate the config file alongside server.py
CONFIG_PATH = Path(__file__).resolve().parent / "filesystem.config.json"

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
def read_file(path: str, offset_bytes: int = 0, max_bytes: int | None = None) -> CallToolResult:
    """Read permitted UTF-8 text; optionally select a bounded byte range.

    Without range arguments, preserve the complete-file 1 MiB limit. Supplying
    max_bytes (1 to 1048576) or a nonzero offset selects a range; the default
    range budget is 65536 bytes. Resume using returned next_offset_bytes, which
    never splits a UTF-8 character or CRLF pair. Ranges do not grant permission.
    """
    
    try:
        if not isinstance(path, str) or not path.strip() or not Path(path).is_absolute():
                raise FilesystemPermissionError("invalid_arguments", "Please supply an absolute file path.")
        if type(offset_bytes) is not int or not 0 <= offset_bytes <= 2**63 - 1:
            raise FilesystemPermissionError("invalid_arguments", "The byte offset must be a nonnegative 64-bit integer.")
        if max_bytes is not None and (type(max_bytes) is not int or not 1 <= max_bytes <= MAX_READ_BYTES):
            raise FilesystemPermissionError("invalid_arguments", "The byte budget must be an integer from 1 to 1048576.")
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
                structuredContent={"kind": "file_contents"},
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


def creation_path(path: str) -> Path:
    """Authorize a new entry without borrowing any of the read root grants."""
    if not isinstance(path, str) or not path.strip() or not Path(path).is_absolute():
        raise FilesystemPermissionError("invalid_arguments", "Please supply an absolute creation path.")
    target = Path(path)
    # Reject ambiguous Windows names on both supported platforms, including
    # alternate data streams, device names and dot/space normalization tricks.
    if any(part in (".", "..") or part.endswith((" ", "."))
           or PureWindowsPath(part).is_reserved() or any(char in part for char in '<>:"|?*')
           or any(ord(char) < 32 for char in part) for part in target.parts[1:]):
        raise FilesystemPermissionError("invalid_arguments", "Please supply a concrete path without traversal or reserved names.")
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
    if target.exists():
        raise FilesystemPermissionError("already_exists", "That path already exists. Nothing was replaced.")
    return target


def creation_result(path: Path, entry_type: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"Created {entry_type}: {path}")],
        structuredContent={"kind": "filesystem_created", "path": str(path), "type": entry_type},
    )


def read_safe_file(path: Path) -> str:
    """Bound the actual read, including files that grow after the size check."""
    max_file_size_b = MAX_READ_BYTES

    if path.stat().st_size > max_file_size_b:
        raise ValueError(f"{path.name} is too big to be read, please open it manually.")
    with path.open("rb") as source:
        data = source.read(max_file_size_b + 1)
    if len(data) > max_file_size_b:
        raise ValueError(f"{path.name} is too big to be read, please open it manually.")
    # Match read_text's universal newline handling without an unbounded text read.
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Explicit filesystem permissions file")
    options = parser.parse_args()
    if options.config is not None:
        CONFIG_PATH = options.config.resolve()
    server.run(transport="stdio")
