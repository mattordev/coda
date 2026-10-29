"""Bounded relative references for the operator-configured bundled filesystem.

This host convenience never supplies permissions: the server still authorizes
the resulting absolute path independently. No directory or resolution is cached.
"""

from copy import deepcopy
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path, PureWindowsPath

from ai.mcp import bundled

MAX_ROOTS = 8
MAX_ENTRIES = 256
MAX_CONFIG_BYTES = 64 * 1024
EXTENSIONS = {'.md', '.txt', '.rst', '.json', '.toml', '.yaml', '.yml', '.py', '.csv'}


class WorkspaceReferenceError(ValueError):
    """An actionable, content-safe failure before any MCP invocation."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise WorkspaceReferenceError(code, message)


def _cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        _fail('cancelled', 'File resolution was cancelled.')


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate configuration key.')
        result[key] = value
    return result


def _configuration(definition):
    """Use the same explicit/default permissions file as the bundled server."""
    try:
        settings = json.loads(definition.settings_json)
        if 'config_file' in settings:
            path = Path(settings['config_file'])
        else:
            bundle = bundled.discover_bundles().bundles['filesystem']
            path = bundle.root / 'filesystem.config.json'
        if not path.is_absolute():
            raise ValueError('Absolute permissions file required.')
        with path.open('rb') as source:
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError('Permissions file too large.')
        read = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_keys)['read']
        allowed, blocked = read['allowed_roots'], read['blocked_paths']
        workspaces = read.get('workspace_roots', [])
        for roots in (allowed, workspaces):
            if not isinstance(roots, list) or any(
                not isinstance(root, str) or not root.strip() or '\0' in root or not Path(root).is_absolute() for root in roots
            ):
                raise ValueError('Roots must be absolute paths.')
        if len(workspaces) > MAX_ROOTS or len(set(workspaces)) != len(workspaces):
            raise ValueError('Too many or duplicate workspaces.')
        if not isinstance(blocked, list) or any(not isinstance(pattern, str) or not pattern.strip() for pattern in blocked):
            raise ValueError('Blocked patterns must be nonempty strings.')
        return [Path(root) for root in workspaces], [Path(root).resolve() for root in allowed], blocked
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError):
        _fail('configuration_error', 'Workspace configuration is missing or invalid. Check the filesystem permissions file.')


def _authorized(path, allowed, blocked):
    """Mirror server containment and lexical/resolved ancestor blocking."""
    lexical = path.absolute()
    resolved = lexical.resolve()
    if not any(resolved.is_relative_to(root) for root in allowed):
        return False
    for pattern in blocked:
        pattern = pattern.replace('\\', '/').casefold()
        for candidate in (lexical, *lexical.parents, resolved, *resolved.parents):
            name = candidate.as_posix().casefold()
            if fnmatchcase(name, pattern) or (pattern.startswith('**/') and fnmatchcase(name, pattern[3:])):
                return False
    return True


def _ordinary_path(path):
    """Relative resolution refuses links/reparse points, including ancestors."""
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if candidate.is_symlink() or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            return False
    return True


def _relative_parts(reference, tool):
    if not reference or len(reference) > 1024 or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in reference):
        _fail('workspace_unsafe', 'Please provide a concrete relative file reference or an absolute path.')
    windows = PureWindowsPath(reference)
    if windows.drive or windows.root or any(char in reference for char in ':*?[]'):
        _fail('workspace_unsafe', 'That file reference is not a safe relative path. Please provide an absolute path.')
    if reference == '.' and tool == 'list_directory':
        return ()
    parts = reference.replace('\\', '/').split('/')
    if any(part in ('', '.', '..') or part.endswith((' ', '.')) or PureWindowsPath(part).is_reserved() for part in parts):
        _fail('workspace_unsafe', 'Relative paths cannot contain traversal or ambiguous components. Please provide an absolute path.')
    return tuple(parts)


def _matches(name, requested, tool):
    if name.casefold() == requested.casefold():
        return True
    return (tool == 'read_file' and '.' not in requested and Path(name).suffix.casefold() in EXTENSIONS
            and Path(name).stem.casefold() == requested.casefold())


def resolve_arguments(definition, tool, arguments, *, cancel_event=None):
    """Resolve only known bundled read tools; leave absolute/other tools intact."""
    if getattr(definition, 'bundled_server', None) != 'filesystem' or tool not in ('read_file', 'list_directory'):
        return arguments
    reference = arguments.get('path')
    if not isinstance(reference, str) or Path(reference).is_absolute():
        return arguments
    _cancelled(cancel_event)
    parts = _relative_parts(reference, tool)
    roots, allowed, blocked = _configuration(definition)
    if not roots:
        _fail('workspace_required', 'No filesystem workspace is configured. Set read.workspace_roots or provide an absolute path.')
    matches = set()
    try:
        for root in roots:
            _cancelled(cancel_event)
            if not _ordinary_path(root) or not _authorized(root, allowed, blocked):
                _fail('workspace_unsafe', 'A configured workspace is linked, blocked or outside allowed roots. Check filesystem configuration.')
            if not root.is_dir():
                _fail('workspace_not_found', 'A configured workspace is unavailable. Check filesystem configuration.')
            parent = root.joinpath(*parts[:-1]) if parts else root
            if not _ordinary_path(parent) or not _authorized(parent, allowed, blocked):
                _fail('workspace_unsafe', 'That relative directory is linked, blocked or outside allowed roots. Please provide an authorized path.')
            if not parent.resolve().is_relative_to(root.resolve()):
                _fail('workspace_unsafe', 'That reference escapes its workspace. Please provide an authorized path.')
            if not parts:
                matches.add(root.resolve())
                continue
            if not parent.is_dir():
                continue
            # Never recurse or stop at the first match: unseen names could be ambiguous.
            with os.scandir(parent) as entries:
                for count, entry in enumerate(entries, start=1):
                    _cancelled(cancel_event)
                    if count > MAX_ENTRIES:
                        _fail('workspace_limit', 'That directory exceeds the file-resolution limit. Please provide an absolute path.')
                    if not _matches(entry.name, parts[-1], tool):
                        continue
                    candidate = parent / entry.name
                    if not _ordinary_path(candidate) or not _authorized(candidate, allowed, blocked):
                        continue
                    canonical = candidate.resolve()
                    if not canonical.is_relative_to(root.resolve()):
                        continue
                    matches_kind = candidate.is_file() if tool == 'read_file' else candidate.is_dir()
                    if matches_kind:
                        matches.add(canonical)
        _cancelled(cancel_event)
        if not matches:
            _fail('workspace_not_found', 'No accessible match was found in the configured workspaces. Please provide a more specific relative path or an absolute path.')
        if len(matches) != 1:
            _fail('workspace_ambiguous', 'More than one accessible match was found. Please provide a more specific relative path or an absolute path.')
        selected = matches.pop()
        # Check current policy again immediately before passing a concrete path onward.
        current_roots, current_allowed, current_blocked = _configuration(definition)
        if roots != current_roots or allowed != current_allowed or blocked != current_blocked:
            _fail('workspace_changed', 'Filesystem configuration changed during resolution. Please request the action again.')
        if not _ordinary_path(selected) or not _authorized(selected, current_allowed, current_blocked):
            _fail('workspace_unsafe', 'The selected path is no longer accessible. Please request the action again.')
        prepared = deepcopy(arguments)
        prepared['path'] = str(selected)
        return prepared
    except (OSError, RuntimeError):
        _fail('workspace_unavailable', 'The workspace could not be inspected safely. Please provide an absolute path or check filesystem configuration.')
