import os


LOW_RISK_THRESHOLD = 0.3
HIGH_RISK_THRESHOLD = 0.7

ACTION_RAW = "raw"
ACTION_SANITIZE = "sanitize"
ACTION_SUMMARIZE = "summarize"
ACTION_BLOCK = "block"

VALID_MODES = {"strict", "balanced", "permissive"}
VALID_ACTIONS = {ACTION_RAW, ACTION_SANITIZE, ACTION_SUMMARIZE, ACTION_BLOCK}


def _get_float_env(name: str, default: float) -> float:
    """
    Read a float env var and fall back safely when it is missing or invalid.

    Privacy settings should never crash startup because of a typo in .env.
    """
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def get_low_risk_threshold() -> float:
    """
    Return the configured boundary between low and medium privacy risk.

    Requests at or below this value are treated as low risk.
    """
    return _get_float_env("CODA_PRIVACY_LOW_RISK_THRESHOLD", LOW_RISK_THRESHOLD)


def get_high_risk_threshold() -> float:
    """
    Return the configured boundary between medium and high privacy risk.

    Requests above this value get the strongest privacy handling.
    """
    return _get_float_env("CODA_PRIVACY_HIGH_RISK_THRESHOLD", HIGH_RISK_THRESHOLD)


def get_privacy_mode() -> str:
    """
    Return the active privacy mode, defaulting to balanced.

    Unknown mode values fall back to balanced so an env typo does not silently
    make privacy more permissive.
    """
    mode = os.getenv("CODA_PRIVACY_MODE", "balanced").strip().lower()
    if mode not in VALID_MODES:
        return "balanced"
    return mode


def get_risk_level(risk: float) -> str:
    """
    Convert a numeric risk score into low, medium, or high.

    Router and detector both call this so the thresholds mean the same thing
    everywhere.
    """
    if risk > get_high_risk_threshold():
        return "high"
    if risk > get_low_risk_threshold():
        return "medium"
    return "low"


def _get_configured_action(name: str, default: str) -> str:
    """
    Read a cloud action env var and fall back if it is not recognised.

    This keeps actions constrained to raw, sanitize, summarize, or block.
    """
    action = os.getenv(name, default).strip().lower()
    if action not in VALID_ACTIONS:
        return default
    return action


def get_cloud_action(privacy_result: dict) -> str:
    """
    Decide how cloud providers may see this privacy result.

    Low risk can be raw, strict mode blocks all sensitive cloud use, balanced
    blocks high risk by default, and permissive allows sanitized high-risk
    fallback unless overridden.
    """
    level = privacy_result["level"]
    mode = get_privacy_mode()

    if level == "low":
        return ACTION_RAW

    if mode == "strict":
        return ACTION_BLOCK

    if level == "high":
        default = ACTION_BLOCK if mode == "balanced" else ACTION_SANITIZE
        return _get_configured_action("CODA_HIGH_RISK_CLOUD_FALLBACK", default)

    return _get_configured_action("CODA_CLOUD_PRIVACY_ACTION", ACTION_SANITIZE)
