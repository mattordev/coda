import json
import threading
import time
from pathlib import Path

import utils.runtime_state as runtime_state

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
_STATE_LOCK = threading.Lock()

# in memory provider state - mirrored to disk so cooldowns survive reloads
_provider_state = {}
_state_loaded = False


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


def _load_state_from_disk():
    if not _STATE_FILE.exists():
        return {}

    try:
        with _STATE_FILE.open("r", encoding="utf-8") as state_file:
            loaded_state = json.load(state_file)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(loaded_state, dict):
        return {}

    provider_state = {}
    for provider, state in loaded_state.items():
        if not isinstance(state, dict):
            continue

        provider_state[provider] = {
            "last_failed_on": _normalize_timestamp(state.get("last_failed_on")),
            "last_attempt": _normalize_timestamp(state.get("last_attempt")),
        }

    return provider_state


def _save_state_to_disk():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _STATE_FILE.with_suffix(".json.tmp")

    serializable_state = {}
    for provider, state in _provider_state.items():
        serializable_state[provider] = {
            "last_failed_on": state.get("last_failed_on"),
            "last_attempt": state.get("last_attempt"),
        }

    with temp_path.open("w", encoding="utf-8") as temp_file:
        json.dump(serializable_state, temp_file, indent=2)

    temp_path.replace(_STATE_FILE)


def _load_state_once():
    global _state_loaded, _provider_state

    if _state_loaded:
        return

    _provider_state = _load_state_from_disk()
    _state_loaded = True


def _ensure_provider(provider):
    """
    Ensure the provider has an entry in the state dict
    
    This allows us to be a little lazy and initialise providers without predefinition.
    """
    _load_state_once()

    if provider not in _provider_state:
        _provider_state[provider] = _default_provider_state()
        _save_state_to_disk()


def log_failure(provider: str, cooldown: int = 30):
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
        _save_state_to_disk()


def log_attempt(provider: str):
    """
    Record that a provider was attempted.
    
    This is used to prevent rapid retry loops where the same provider
    is called repeatedly in a short period of time.
    """
    with _STATE_LOCK:
        _ensure_provider(provider)
        _provider_state[provider]["last_attempt"] = time.time()
        _save_state_to_disk()


def should_skip_provider(provider: str, cooldown: int = 30) -> bool:
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