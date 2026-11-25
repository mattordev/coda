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
def read_file(path: str) -> CallToolResult:
    """Read a specified file."""
    
    try:
        if not path.strip() or not Path(path).is_absolute():
                raise FilesystemPermissionError("invalid_arguments", "Please supply an absolute file path.")
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
            text = read_safe_file(requested_path)
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
    max_file_size_mib = 1
    max_file_size_b = max_file_size_mib * 1024 * 1024   

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Explicit filesystem permissions file")
    options = parser.parse_args()
    if options.config is not None:
        CONFIG_PATH = options.config.resolve()
    server.run(transport="stdio")
