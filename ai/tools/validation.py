"""Offline JSON Schema validation for external tool arguments."""

from copy import deepcopy
from math import isfinite

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from referencing import Registry


def _is_json_value(value: object) -> bool:
    """Reject values which cannot be carried unchanged in a JSON message."""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in value.items()
        )
    return False


def compile_input_schema(schema: dict[str, object]) -> Validator:
    """Validate and snapshot an object schema without retrieving references."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("Tool input schema must describe an object.")
    try:
        if not _is_json_value(schema):
            raise ValueError("Tool input schema must contain JSON values.")
        if "$schema" in schema and not isinstance(schema["$schema"], str):
            raise ValueError("Tool input schema dialect must be a string.")
        snapshot = deepcopy(schema)
        default = None if "$schema" in snapshot else Draft202012Validator
        validator_type = validator_for(snapshot, default=default)
        if validator_type is None:
            raise ValueError("Tool input schema uses an unsupported dialect.")
        validator_type.check_schema(snapshot)
    except (SchemaError, RecursionError) as exc:
        raise ValueError("Tool input schema is invalid.") from exc

    # An empty registry has no network or filesystem retrieval callback.
    return validator_type(snapshot, registry=Registry())


def validate_arguments(
    validator: Validator,
    arguments: dict[str, object] | None,
) -> dict[str, object]:
    """Return a validated snapshot; do not coerce types or insert defaults."""
    values = {} if arguments is None else arguments
    if not isinstance(values, dict):
        raise ValueError("Tool arguments must be an object.")
    try:
        if not _is_json_value(values):
            raise ValueError("Tool arguments must contain JSON values.")
        snapshot = deepcopy(values)
        validator.validate(snapshot)
    except (ValidationError, RecursionError) as exc:
        # Validator messages may contain sensitive argument values.
        raise ValueError("Tool arguments do not match the input schema.") from exc
    return snapshot
