"""Standalone, read-only filesystem MCP with explicit local permissions."""

import argparse
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from os import listdir
from os.path import isdir, isfile, join
from pathlib import Path
import json
from fnmatch import fnmatchcase


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


def error_result(code, message):
    return CallToolResult(
        isError=True, content=[TextContent(type="text", text=message)],
        structuredContent={"error": {"code": code, "message": message}},
    )


def directory_entries(path: str) -> list[dict[str, str]]:
    """Validate once per request, then filter children using the same policy."""
    if not path.strip() or not Path(path).is_absolute():
        raise FilesystemPermissionError("invalid_arguments", "Please supply an absolute directory path.")
    config = load_config()
    check_if_path_is_allowed(config, path)
    check_if_path_is_blocked(config, path)
    requested_path = Path(path).resolve()
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
        except PermissionError:
            continue

        if isfile(full_path):
            response.append({"name": entry, "type": "file"})
        elif isdir(full_path):
            response.append({"name": entry, "type": "directory"})

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Explicit filesystem permissions file")
    options = parser.parse_args()
    if options.config is not None:
        CONFIG_PATH = options.config.resolve()
    server.run(transport="stdio")
