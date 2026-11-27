"""Operator-owned exact action grants. Tool/model data supplies only action IDs."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re


class SystemPolicyError(ValueError):
    """Safe policy rejection before any executable is launched."""


@dataclass(frozen=True)
class ApprovedAction:
    name: str
    executable: str
    argv: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    timeout_seconds: float
    max_output_bytes: int
    background: bool
    scheduled: bool
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SystemPolicy:
    enabled: bool
    fingerprint: str
    actions: tuple[ApprovedAction, ...]
    secrets: tuple[str, ...]

    def action(self, name, *, background=False, scheduled=False):
        if not self.enabled:
            raise SystemPolicyError('System execution is disabled.')
        found = next((action for action in self.actions if action.name == name), None)
        if found is None or background and not found.background or scheduled and not found.scheduled:
            raise SystemPolicyError('This action is not permitted for the requested execution mode.')
        return found


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemPolicyError('Duplicate policy key.')
        result[key] = value
    return result


def _ordinary_path(value, *, directory=False):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise SystemPolicyError('Action paths must be absolute operator paths.')
    path = Path(value)
    for parent in (path, *path.parents):
        if parent.is_symlink() or (parent.exists() and getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400):
            raise SystemPolicyError('Action paths cannot traverse links or junctions.')
    if not (path.is_dir() if directory else path.is_file()):
        raise SystemPolicyError('An action path is unavailable.')
    if not directory and path.suffix.lower() in ('.bat', '.cmd', '.ps1', '.sh'):
        raise SystemPolicyError('Shell script entry points are not supported.')
    return str(path.resolve())


def load_system_policy(path):
    """Only fixed argv/cwd and named environment references; no PATH lookup/shell."""
    with Path(path).open('rb') as source:
        encoded = source.read(65537)
    if len(encoded) > 65536:
        raise SystemPolicyError('System policy exceeds its size limit.')
    raw = json.loads(encoded, object_pairs_hook=_unique)
    if not isinstance(raw, dict) or set(raw) - {'enabled', 'actions', 'secret_env'}:
        raise SystemPolicyError('Invalid system policy fields.')
    enabled = raw.get('enabled', False)
    if type(enabled) is not bool:
        raise SystemPolicyError('Execution enablement must be a boolean.')
    entries = raw.get('actions', {})
    if not isinstance(entries, dict) or len(entries) > 32:
        raise SystemPolicyError('Supply at most 32 named operator actions.')
    secret_names = raw.get('secret_env', [])
    if not isinstance(secret_names, list) or len(secret_names) > 32 or any(
            not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
            for name in secret_names):
        raise SystemPolicyError('Invalid secret environment references.')
    secrets = tuple(os.environ[name] for name in secret_names if os.environ.get(name))
    actions = []
    identities = {}
    bytes_hashed = 0

    def identity(filename):
        nonlocal bytes_hashed
        if filename not in identities:
            digest = hashlib.sha256()
            total = 0
            with open(filename, 'rb') as source:
                while chunk := source.read(65536):
                    total += len(chunk)
                    bytes_hashed += len(chunk)
                    if total > 64 * 1024 * 1024:
                        raise SystemPolicyError('An executable or reviewed file exceeds 64 MiB.')
                    if bytes_hashed > 256 * 1024 * 1024:
                        raise SystemPolicyError('Reviewed action files exceed 256 MiB in total.')
                    digest.update(chunk)
            identities[filename] = digest.hexdigest()
        return filename, identities[filename]

    for name, entry in entries.items():
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', name) or not isinstance(entry, dict) or set(entry) - {
                'executable', 'argv', 'cwd', 'env', 'timeout_seconds', 'max_output_bytes', 'background', 'scheduled', 'files'}:
            raise SystemPolicyError('Invalid operator action.')
        executable = _ordinary_path(entry.get('executable'))
        cwd = _ordinary_path(entry.get('cwd'), directory=True)
        files = entry.get('files', [])
        if not isinstance(files, list) or len(files) > 16:
            raise SystemPolicyError('Supply at most 16 reviewed action files.')
        reviewed = tuple(identity(_ordinary_path(file)) for file in files)
        reviewed = (identity(executable), *reviewed)
        argv = entry.get('argv', [])
        if not isinstance(argv, list) or len(argv) > 64 or any(
                not isinstance(arg, str) or '\0' in arg or len(arg) > 4096 for arg in argv) or sum(map(len, argv)) > 16000:
            raise SystemPolicyError('Invalid bounded action argv.')
        refs = entry.get('env', {})
        if not isinstance(refs, dict) or len(refs) > 32 or any(
                not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not isinstance(value, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value) or value not in os.environ
                for key, value in refs.items()):
            raise SystemPolicyError('Action environment references are unavailable or invalid.')
        environment = tuple(sorted((key, os.environ[value]) for key, value in refs.items()))
        secrets = (*secrets, *(value for _, value in environment if value))
        timeout, output = entry.get('timeout_seconds', 30), entry.get('max_output_bytes', 16384)
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600 or type(output) is not int or not 256 <= output <= 65536:
            raise SystemPolicyError('Invalid action resource limits.')
        background, scheduled = entry.get('background', False), entry.get('scheduled', False)
        if type(background) is not bool or type(scheduled) is not bool:
            raise SystemPolicyError('Action modes must be explicit booleans.')
        actions.append(ApprovedAction(name, executable, tuple(argv), cwd, environment, timeout, output, background, scheduled, reviewed))
    secrets = tuple(sorted(set(secrets), key=lambda value: (-len(value), value)))
    binding = encoded + repr((actions, secrets)).encode('utf-8')
    return SystemPolicy(enabled, hashlib.sha256(binding).hexdigest(), tuple(actions), secrets)
