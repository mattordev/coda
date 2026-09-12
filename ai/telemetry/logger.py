import json
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import utils.runtime_state as runtime_state
from ai.telemetry.models import ProviderAttempt

"""
Telemetry module for tracking provider health and performance.

Current capabilities:
- failure tracking (last_failed_on)
- cooldown-based skipping

Future extensions:
- latency tracking
- success rates
- persistence
"""

_STATE_FILE = Path(__file__).resolve().parents[2] / "telemetry_state.json"
_TELEMETRY_LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "telemetry"
_ACTIVE_SESSION_FILE = _TELEMETRY_LOG_DIR / "active-session.jsonl"
_STATE_LOCK = threading.Lock()
_STATE_SCHEMA_VERSION = 1
_MAX_SESSION_ATTEMPTS = 1000

# In-memory provider cooldown state, preserved from the legacy format.
_provider_state = {}

# Serialized attempt records for the active session only.
_attempts = []
_attempts_pending_state_migration = False

_state_loaded = False
_session_ready = False


def _debug_print(*args, **kwargs):
    runtime_state.debug_print(*args, **kwargs)


def _default_provider_state():
    return {
        "last_failed_on": None,
        "last_attempt": None,
    }


def _normalize_timestamp(value):
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_provider_state(raw_state):
    """Return safe cooldown state from raw json data."""
    if not isinstance(raw_state, dict):
        return {}

    provider_state = {}

    for provider, state in raw_state.items():
        if not isinstance(provider, str):
            continue

        if not isinstance(state, dict):
            continue

        provider_state[provider] = {
            "last_failed_on": _normalize_timestamp(
                state.get("last_failed_on")
            ),
            "last_attempt": _normalize_timestamp(
                state.get("last_attempt")
            ),
        }

    return provider_state


def _archive_path(interrupted=False):
    """Return an unused UTC-dated path for a completed session log."""
    suffix = "-interrupted" if interrupted else ""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    candidate = _TELEMETRY_LOG_DIR / f"{timestamp}{suffix}.jsonl"
    sequence = 2

    while candidate.exists():
        candidate = _TELEMETRY_LOG_DIR / (
            f"{timestamp}{suffix}-{sequence}.jsonl"
        )
        sequence += 1

    return candidate


def _prepare_session():
    """Recover a previous process's unfinished active session once."""
    global _session_ready

    if _session_ready:
        return

    _TELEMETRY_LOG_DIR.mkdir(parents=True, exist_ok=True)

    if _ACTIVE_SESSION_FILE.exists() and _ACTIVE_SESSION_FILE.stat().st_size:
        _ACTIVE_SESSION_FILE.replace(_archive_path(interrupted=True))

    _session_ready = True


def _save_session_to_disk():
    """Atomically replace the bounded active-session JSONL file."""
    _prepare_session()
    temp_path = _ACTIVE_SESSION_FILE.with_name(
        f"{_ACTIVE_SESSION_FILE.name}.tmp"
    )

    with temp_path.open("w", encoding="utf-8") as temp_file:
        for attempt in _attempts[-_MAX_SESSION_ATTEMPTS:]:
            json.dump(attempt, temp_file, separators=(",", ":"))
            temp_file.write("\n")

    temp_path.replace(_ACTIVE_SESSION_FILE)


def _save_session_safely():
    try:
        _save_session_to_disk()
        return True
    except OSError as error:
        _debug_print(f"[DEBUG] Could not save telemetry session: {error}")
        return False


def _save_state_safely():
    try:
        _save_state_to_disk()
    except OSError as error:
        _debug_print(f"[DEBUG] Could not save telemetry state: {error}")


def _load_state_from_disk():
    """Load legacy or versioned telemetry state safely."""
    if not _STATE_FILE.exists():
        return {}, []

    try:
        with _STATE_FILE.open("r", encoding="utf-8") as state_file:
            loaded_state = json.load(state_file)
    except (OSError, json.JSONDecodeError):
        return {}, []

    if not isinstance(loaded_state, dict):
        return {}, []

    if "schema_version" not in loaded_state:
        return _normalize_provider_state(loaded_state), []

    schema_version = loaded_state.get("schema_version")

    if (
        isinstance(schema_version, bool)
        or schema_version != _STATE_SCHEMA_VERSION
    ):
        return {}, []

    attempts = loaded_state.get("attempts", [])

    if not isinstance(attempts, list):
        attempts = []

    return (
        _normalize_provider_state(loaded_state.get("provider_state")),
        attempts,
    )


def _save_state_to_disk():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _STATE_FILE.with_suffix(".json.tmp")

    serializable_provider_state = {}

    for provider, state in _provider_state.items():
        serializable_provider_state[provider] = {
            "last_failed_on": state.get("last_failed_on"),
            "last_attempt": state.get("last_attempt"),
        }

    serializable_state = {
        "schema_version": _STATE_SCHEMA_VERSION,
        "provider_state": serializable_provider_state,
    }

    if _attempts_pending_state_migration:
        serializable_state["attempts"] = _attempts

    with temp_path.open("w", encoding="utf-8") as temp_file:
        json.dump(serializable_state, temp_file, indent=2)

    temp_path.replace(_STATE_FILE)


def _load_state_once():
    global _state_loaded, _provider_state, _attempts
    global _attempts_pending_state_migration

    if _state_loaded:
        return

    _provider_state, _attempts = _load_state_from_disk()
    _state_loaded = True

    if _attempts:
        _attempts = [
            attempt for attempt in _attempts
            if isinstance(attempt, dict)
        ][-_MAX_SESSION_ATTEMPTS:]
        _attempts_pending_state_migration = bool(_attempts)

        if _attempts_pending_state_migration and _save_session_safely():
            _attempts_pending_state_migration = False
            _save_state_safely()


def _ensure_provider(provider):
    """
    Ensure the provider has an entry in the state dict
    
    This allows us to be a little lazy and initialise providers without predefinition.
    """
    _load_state_once()

    if provider not in _provider_state:
        _provider_state[provider] = _default_provider_state()
        _save_state_safely()


def log_failure(provider: str):
    """
    Record that a provider has failed, with a timestamp
    
    This is used so the router doesn't keep trying a bad provider.
    """
    with _STATE_LOCK:
        _ensure_provider(provider)

        now = time.time()

        # Refresh the failure window on every failure so repeated retries
        # do not collapse the cooldown down to the first failure only.
        _provider_state[provider]["last_failed_on"] = now
        _save_state_safely()


def log_attempt(provider: str):
    """
    Record that a provider was attempted.
    
    This is used to prevent rapid retry loops where the same provider
    is called repeatedly in a short period of time.
    """
    with _STATE_LOCK:
        _ensure_provider(provider)
        _provider_state[provider]["last_attempt"] = time.time()
        _save_state_safely()


def record_attempt(attempt: ProviderAttempt):
    """Persist one privacy-safe attempt without risking the provider request."""
    global _attempts_pending_state_migration

    if not isinstance(attempt, ProviderAttempt):
        raise TypeError("attempt must be a ProviderAttempt.")

    with _STATE_LOCK:
        _attempts.append(asdict(attempt))
        del _attempts[:-_MAX_SESSION_ATTEMPTS]

        if _save_session_safely() and _attempts_pending_state_migration:
            _attempts_pending_state_migration = False
            _save_state_safely()


def finalize_session():
    """Archive the active telemetry file at graceful shutdown."""
    global _session_ready

    with _STATE_LOCK:
        try:
            if (
                not _ACTIVE_SESSION_FILE.exists()
                or not _ACTIVE_SESSION_FILE.stat().st_size
            ):
                return None

            destination = _archive_path()
            _ACTIVE_SESSION_FILE.replace(destination)
            _attempts.clear()
            _session_ready = False
            return destination
        except OSError as error:
            _debug_print(f"[DEBUG] Could not archive telemetry session: {error}")
            return None


def should_skip_provider(provider: str, cooldown: int = 600) -> bool:
    """
    Check if a provider should be skipped based on recent failures or attempts

    Args:
        provider: The provider name (e.g. "openai", "ollama")
        cooldown: Time in seconds to consider a provider "unhealthy"

    Returns:
        True if the provider should be skipped,
        False otherwise.
    """
    with _STATE_LOCK:
        _ensure_provider(provider)

        _debug_print(f"[DEBUG] State for {provider}: {_provider_state.get(provider)}")

        now = time.time()

        last_failed = _provider_state[provider]["last_failed_on"]
        last_attempt = _provider_state[provider]["last_attempt"]

        # Normal failure cooldown
        if last_failed and (now - last_failed) < cooldown:
            elapsed = now - last_failed
            remaining = cooldown - elapsed
            _debug_print(
                f"[DEBUG] Skipping {provider}: last failure {elapsed:.1f}s ago, "
                f"{remaining:.1f}s remaining in cooldown."
            )
            return True

        # Prevent rapid retry loop by enforcing a short cooldown after any attempt
        if last_attempt and (now - last_attempt) < 2:
            elapsed = now - last_attempt
            _debug_print(
                f"[DEBUG] Skipping {provider}: last attempt {elapsed:.1f}s ago."
            )
            return True

        return False
