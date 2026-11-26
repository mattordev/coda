"""Host-owned mutation exclusions derived from operator registry provenance.

These are additional denials, never read/write grants or model arguments. The
standalone server receives the same exclusions so descendant preflight also
catches physical aliases that a top-level host path check cannot enumerate.
"""

from dataclasses import replace
import json
import os
from pathlib import Path

from ai.mcp import bundled

MUTATIONS = frozenset({'create_file', 'create_directory', 'edit_file', 'append_file',
                       'copy_path', 'move_path', 'delete_path'})
MAX_GUARD_CHARS = 16384
MAX_GUARD_PATHS = 128


class OperatorProtectionError(ValueError):
    """Operator exclusions could not be prepared; no process may be invoked."""


def operator_paths(definitions):
    """Protect complete definition directories, installation and declared files."""
    paths = {bundled.INSTALL_ROOT.resolve(), Path(os.getenv(
        'CODA_MCP_CONFIG_DIR', str(bundled.INSTALL_ROOT / 'mcp_servers'))).resolve()}
    catalog = bundled.discover_bundles()
    for definition in definitions:
        origin = getattr(definition, 'definition_file', None)
        if origin:
            paths.add(Path(origin).resolve().parent)
        identity = getattr(definition, 'bundled_server', None)
        if identity is None:
            continue
        bundle = catalog.bundles.get(identity)
        if bundle is None:
            continue
        paths.add(bundle.root.resolve())
        settings = json.loads(definition.settings_json)
        if not isinstance(settings, dict):
            raise ValueError('Invalid operator bundle settings.')
        effective = bundle.manifest.get('defaults', {}) | settings
        for check in bundle.manifest.get('configuration_files', []):
            if check['setting'] in effective:
                value = effective[check['setting']]
                # Invalid disabled/isolated bundle settings cannot launch a
                # process or name an authority file. Preserve isolation while
                # retaining every valid absolute path, even if its file is bad.
                if not isinstance(value, str) or '\0' in value or not Path(value).is_absolute():
                    continue
                candidate = Path(value)
            else:
                candidate = bundle.root / check['default']
            paths.add(candidate.resolve())
    # Reduce redundant descendants so launch metadata stays small. Reserve a
    # declared file's future descendants too; it can never be a data directory.
    paths = sorted((path for path in paths if not any(
        other != path and path.is_relative_to(other) for other in paths)), key=str)
    encoded = json.dumps([str(path) for path in paths], ensure_ascii=True)
    if len(paths) > MAX_GUARD_PATHS or len(encoded) > MAX_GUARD_CHARS:
        raise ValueError('Operator protection metadata exceeds its bounded launch limit.')
    return tuple(paths), encoded


def protected_target(target, paths, *, contains=False):
    """Check lexical/resolved containment and existing target/ancestor aliases."""
    lexical = Path(target).absolute()
    resolved = lexical.resolve()
    for path in paths:
        if any(candidate.is_relative_to(path) or (contains and path.is_relative_to(candidate))
               for candidate in (lexical, resolved)):
            return True
    identities = set()
    for path in paths:
        try:
            metadata = path.stat()
        except FileNotFoundError:
            continue
        identities.add((metadata.st_dev, metadata.st_ino))
    for ancestor in (lexical, *lexical.parents):
        try:
            metadata = ancestor.stat()
        except FileNotFoundError:
            continue
        if (metadata.st_dev, metadata.st_ino) in identities:
            return True
    return False


def check_mutation(tool, arguments, paths):
    """Inspect only fixed schema fields; source reads do not confer mutation."""
    if not isinstance(arguments, dict):
        return False
    targets = [('destination', False)] if tool == 'copy_path' else (
        [('source', True), ('destination', False)] if tool == 'move_path'
        else [('path', tool == 'delete_path')])
    return any(isinstance(arguments.get(key), str) and Path(arguments[key]).is_absolute()
               and protected_target(arguments[key], paths, contains=contains) for key, contains in targets)


def guarded_launch(definition, definitions):
    """Only the trusted bundled filesystem understands this internal launch flag."""
    try:
        _, encoded = operator_paths(definitions)
    except (OSError, ValueError, TypeError, RuntimeError, KeyError) as error:
        raise OperatorProtectionError('Operator exclusions are invalid or exceed the launch limit.') from error
    return replace(definition, args=(*definition.args, '--operator-paths-json', encoded))
