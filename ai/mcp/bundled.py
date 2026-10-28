"""Data-only first-party catalog; metadata never grants process or tool trust."""

from copy import deepcopy
from dataclasses import dataclass, field
import importlib.util
import json
from pathlib import Path, PureWindowsPath
import re
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

INSTALL_ROOT = Path(__file__).resolve().parents[2]
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def read_json(path):
    """Reject duplicate keys at every depth."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def reject_link(path):
    if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("Bundle paths cannot contain links or junctions.")


def asset(root, relative):
    """Only ordinary files beneath a bundle; check Windows syntax on all hosts."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("Invalid bundle asset path.")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts) or PureWindowsPath(relative).is_absolute():
        raise ValueError("Invalid bundle asset path.")
    reject_link(root)
    candidate = root
    for part in parts:
        candidate = candidate / part
        reject_link(candidate)
    if not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError("Bundle asset missing or unsafe; reinstall the bundle.")
    return candidate.resolve()


def _local_schema(schema):
    # Schemas cannot perform network access while validating settings.
    if isinstance(schema, dict):
        if any(key in schema for key in ("$ref", "$dynamicRef")):
            raise ValueError("Bundle schemas must be self-contained.")
        for value in schema.values():
            _local_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            _local_schema(value)


@dataclass(frozen=True)
class Bundle:
    identity: str
    version: str
    root: Path
    manifest: dict = field(repr=False)
    error: str | None = None

    def launch(self, settings=None):
        """Validate settings without importing a server or resolving credentials."""
        if self.error:
            raise ValueError(self.error)
        settings = {} if settings is None else settings
        if not isinstance(settings, dict):
            raise ValueError("Bundle settings must be an object.")
        effective = deepcopy(self.manifest.get("defaults", {})) | deepcopy(settings)
        if not Draft202012Validator(self.manifest["settings_schema"]).is_valid(effective):
            raise ValueError("Bundle settings invalid; check the bundle documentation.")
        script = asset(self.root, self.manifest["entrypoint"])
        if not sys.executable or not Path(sys.executable).is_file():
            raise ValueError("Bundle interpreter missing; repair the Coda environment.")
        args = [str(script)]
        for name, flag in self.manifest.get("setting_arguments", {}).items():
            if name in effective:
                value = effective[name]
                if not isinstance(value, str) or "\0" in value:
                    raise ValueError("Bundle argument settings must be strings.")
                args.extend((flag, value))
        for check in self.manifest.get("configuration_files", []):
            if check["setting"] in effective:
                path = Path(effective[check["setting"]])
                if not path.is_absolute():
                    raise ValueError("Bundle configuration paths must be absolute.")
                reject_link(path)
            else:
                path = asset(self.root, check["default"])
            try:
                config = read_json(path)
                if not Draft202012Validator(check["schema"]).is_valid(config):
                    raise ValueError("Invalid configuration.")
            except (OSError, ValueError, TypeError) as error:
                raise ValueError("Bundle permissions/settings file missing or invalid; configure it before enabling.") from error
        return sys.executable, tuple(args), json.dumps(effective, allow_nan=False)


@dataclass(frozen=True)
class BundleCatalog:
    bundles: dict[str, Bundle]
    errors: dict[str, str]


def discover_bundles():
    """Scan metadata/assets only; no server imports, subprocesses or network."""
    root = INSTALL_ROOT / "integrations"
    bundles, errors, duplicates, seen = {}, {}, set(), set()
    if not root.exists():
        return BundleCatalog(bundles, errors)
    reject_link(root)
    for directory in sorted(root.iterdir()):
        path = directory / "manifest.json"
        identity = None
        try:
            reject_link(directory)
            reject_link(path)
            if not path.exists():
                continue
            reject_link(path)
            value = read_json(path)
            allowed = {"id", "version", "entrypoint", "settings_schema", "defaults",
                       "setting_arguments", "configuration_files", "dependencies"}
            if not isinstance(value, dict) or set(value) - allowed:
                raise ValueError("Unsupported manifest fields.")
            identity = value["id"]
            if not isinstance(identity, str) or not IDENTIFIER.fullmatch(identity):
                identity = None
                raise ValueError("Invalid bundle id.")
            if identity in seen:
                duplicates.add(identity)
                bundles.pop(identity, None)
                raise ValueError("Duplicate bundle id.")
            seen.add(identity)
            if not isinstance(value["version"], str) or not re.fullmatch(r"\d+\.\d+\.\d+", value["version"]):
                raise ValueError("Invalid bundle version.")
            schema = value["settings_schema"]
            _local_schema(schema)
            Draft202012Validator.check_schema(schema)
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                raise ValueError("Settings schema must reject unknown properties.")
            defaults, properties = value.get("defaults", {}), schema.get("properties", {})
            security_fields = {"trusted", "enabled", "allowed_tools", "confirm_tools", "allowed_roots", "blocked_paths"}
            if not isinstance(defaults, dict) or any(
                key in security_fields or isinstance(defaults[key], (dict, list))
                or key not in properties or properties[key].get("security", True) is not False
                or not Draft202012Validator(properties[key]).is_valid(defaults[key]) for key in defaults
            ):
                raise ValueError("Only explicitly non-security defaults are allowed.")
            mappings = value.get("setting_arguments", {})
            if not isinstance(mappings, dict) or any(
                key not in properties or not isinstance(flag, str) or not re.fullmatch(r"--[a-z][a-z-]*", flag)
                for key, flag in mappings.items()
            ):
                raise ValueError("Invalid settings argument mapping.")
            checks = value.get("configuration_files", [])
            if not isinstance(checks, list):
                raise ValueError("Invalid configuration file checks.")
            for check in checks:
                if not isinstance(check, dict) or set(check) != {"setting", "default", "schema"} or check["setting"] not in mappings:
                    raise ValueError("Invalid configuration file check.")
                _local_schema(check["schema"])
                Draft202012Validator.check_schema(check["schema"])
            dependencies = value.get("dependencies", [])
            if not isinstance(dependencies, list) or any(
                not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                for name in dependencies
            ):
                raise ValueError("Dependencies must be top-level module names.")
            availability = None
            try:
                asset(directory, value["entrypoint"])
                if not sys.executable or not Path(sys.executable).is_file():
                    raise ValueError("Interpreter unavailable; repair Coda's environment.")
                if any(importlib.util.find_spec(name) is None for name in dependencies):
                    raise ValueError("Bundle dependency missing; install Coda's locked requirements.")
            except ValueError as error:
                availability = str(error)
            except (OSError, ImportError):
                availability = "Bundle assets/dependencies unavailable; repair the installed bundle and Coda environment."
            bundles[identity] = Bundle(identity, value["version"], directory, value, availability)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, SchemaError):
            errors[directory.name] = "Invalid bundle manifest; repair the installed bundle metadata."
    for identity in duplicates:
        bundles.pop(identity, None)
    return BundleCatalog(bundles, errors)


def bundled_launch(bundle_id, settings=None):
    """Compatibility helper returning interpreter and absolute arguments."""
    bundle = discover_bundles().bundles.get(bundle_id) if isinstance(bundle_id, str) else None
    if bundle is None:
        raise ValueError("Unknown or invalid bundle; inspect the installed catalog.")
    command, args, _ = bundle.launch(settings)
    return command, args


def inspect_bundles():
    """A safe public snapshot: settings, filesystem roots and credentials omitted."""
    catalog = discover_bundles()
    return {
        "bundles": {identity: {"version": bundle.version,
                               "availability": "error" if bundle.error else "available",
                               "error": bundle.error}
                    for identity, bundle in catalog.bundles.items()},
        "errors": catalog.errors,
    }


if __name__ == "__main__":
    from ai.mcp.runtime import LocalMCPManager
    manager, definition_errors = LocalMCPManager.from_directory()
    print(json.dumps({**inspect_bundles(), "instances": manager.inspect(),
                      "definition_errors": {name: error.message for name, error in definition_errors.items()}}, indent=2))
