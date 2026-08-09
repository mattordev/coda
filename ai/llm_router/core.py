import os
from threading import Event

from ai.providers import registry
from ai.privacy.detector import analyze_privacy
from ai.privacy import policy
from ai.telemetry import logger
import utils.llm_service as llm_service
import utils.runtime_state as runtime_state


def _debug_print(*args, **kwargs):
    runtime_state.debug_print(*args, **kwargs)


def _is_cancelled(cancel_event: Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _try_providers(
    providers: list[str],
    prompt: str,
    risk: float,
    privacy_result=None,
    cancel_event: Event | None = None,
):
    """
    Attempt providers in order and return the first successful response.

    The same privacy result is passed to every provider attempt so fallback
    calls do not lose the sanitisation context from the original prompt.
    """
    last_error = None

    for provider in providers:
        if _is_cancelled(cancel_event):
            _debug_print(
                "[ROUTER] Request cancelled before next provider."
            )
            return None, "Request cancelled."

        _debug_print(f"[ROUTER] trying provider {provider}")

        response, error, skipped = _call_provider_with_health(provider, prompt, risk, privacy_result)

        if _is_cancelled(cancel_event):
            _debug_print(
                "[ROUTER] Request cancelled after provider attempt."
            )
            return None, "Request cancelled."

        if skipped:
            _debug_print(f"[ROUTER] {provider} skipped due to recent failure.")
            continue

        if error or not response:
            logger.log_failure(provider)
            _debug_print(f"[ROUTER] {provider} failed: {error}")
            last_error = error
            continue

        _debug_print(f"[ROUTER] {provider} succeeded")
        return response, None

    return None, last_error


def _call_provider_with_health(provider: str, prompt: str, risk: float, privacy_result=None):
    """
    Call one provider unless telemetry says it is temporarily unhealthy.

    This keeps cooldown handling in the router while llm_service handles
    conversation history and provider-specific rendering.
    """
    if logger.should_skip_provider(provider):
        _debug_print(
            f"[ROUTER] Skipping {provider} due to recent failure; using fallback."
        )
        return None, f"{provider} is temporarily unavailable after a recent failure.", True

    logger.log_attempt(provider)
    response, error = llm_service.call_provider(
        provider,
        prompt,
        risk=risk,
        privacy_result=privacy_result,
    )
    return response, error, False


def _high_risk_unavailable_message():
    return (
        "I'm unable to process that request right now because my local AI provider is "
        "unavailable and the request appears to contain sensitive information."
    )


def _provider_unavailable_message():
    return (
        "I'm unable to process that request right now because my AI providers are "
        "unavailable."
    )


def _no_local_provider_configured_message():
    return (
        "No local AI provider is configured, so I can't process sensitive requests. "
        "Check CODA_LOCAL_PROVIDERS."
    )


def _no_provider_configured_message():
    return (
        "No AI providers are configured for this request. Check "
        "CODA_CLOUD_PROVIDERS and CODA_LOCAL_PROVIDERS."
    )


def _split_provider_list(value: str) -> list[str]:
    return [
        provider.strip()
        for provider in value.split(",")
        if provider.strip()
    ]


def _get_configured_provider_groups():
    cloud_provider_names = _split_provider_list(
        os.getenv("CODA_CLOUD_PROVIDERS", "")
    )
    local_provider_names = _split_provider_list(
        os.getenv("CODA_LOCAL_PROVIDERS", "")
    )

    if cloud_provider_names:
        cloud_providers = registry.get_configured_providers(
            cloud_provider_names,
            provider_type="cloud",
        )
    else:
        cloud_providers = registry.get_providers_by_type("cloud")

    if local_provider_names:
        local_providers = registry.get_configured_providers(
            local_provider_names,
            provider_type="local",
        )
    else:
        local_providers = registry.get_providers_by_type("local")

    return cloud_providers, local_providers


def get_provider_order(privacy_result):
    """
    Build the provider attempt order from privacy level and cloud policy.

    Float input is still supported for older tests/helpers, but normal routing
    now passes the full privacy result from analyze_privacy().
    """
    cloud_providers, local_providers = _get_configured_provider_groups()

    if isinstance(privacy_result, (int, float)):
        privacy_result = {
            "risk": float(privacy_result),
            "level": policy.get_risk_level(float(privacy_result)),
            "categories": [],
            "matches": [],
        }

    level = privacy_result["level"]
    cloud_action = policy.get_cloud_action(privacy_result)

    if level == "high":
        if cloud_action == policy.ACTION_BLOCK:
            return local_providers
        return local_providers + cloud_providers

    if level == "medium":
        if cloud_action == policy.ACTION_BLOCK:
            return local_providers
        return local_providers + cloud_providers

    return cloud_providers + local_providers


def route_request(
    prompt: str,
    cancel_event: Event | None = None,
):
    """
    Analyse privacy, choose provider order, and return the first good response.

    Routing decides whether cloud providers are allowed, while llm_service
    decides what cloud-safe content those providers actually receive.
    """
    privacy_result = analyze_privacy(prompt)
    risk = privacy_result["risk"]

    providers = get_provider_order(privacy_result)

    _debug_print(f"[DEBUG - ROUTER] Privacy risk: {risk}")
    _debug_print(f"[DEBUG - ROUTER] Provider order: {providers}")

    # High risk, LOCAL ONLY when cloud policy blocks fallback
    if (
        privacy_result["level"] == "high"
        and policy.get_cloud_action(privacy_result) == policy.ACTION_BLOCK
    ):
        if not providers:
            _debug_print("[ROUTER] High risk -> no local providers configured.")
            return _no_local_provider_configured_message(), None

        _debug_print("[ROUTER] High risk -> using LOCAL providers only")

        response, error = _try_providers(
            providers,
            prompt,
            risk,
            privacy_result,
            cancel_event=cancel_event,
        )

        if response:
            return response, None

        return _high_risk_unavailable_message(), None

    # Sensitive request, try providers in policy order
    if privacy_result["level"] in ("medium", "high"):
        if not providers:
            _debug_print("[ROUTER] Medium risk -> no providers configured.")
            return _no_provider_configured_message(), None

        _debug_print(f"[ROUTER] Medium risk -> trying providers in order: {providers}")

        response, error = _try_providers(
            providers,
            prompt,
            risk,
            privacy_result,
            cancel_event=cancel_event,
        )

        if response:
            return response, None

        _debug_print("[ROUTER] All providers failed -> giving up")
        return _provider_unavailable_message(), None

    # Low risk, CLOUD first, fallback to LOCAL
    if not providers:
        _debug_print("[ROUTER] Low risk -> no providers configured.")
        return _no_provider_configured_message(), None

    _debug_print(f"[DEBUG] Primary provider being checked: {providers[0]}")
    _debug_print(f"[ROUTER] Low risk -> trying providers in order: {providers}")

    response, error = _try_providers(
        providers,
        prompt,
        risk,
        privacy_result,
        cancel_event=cancel_event,
    )

    if response:
        return response, None

    _debug_print("[ROUTER] All providers failed -> giving up")
    return _provider_unavailable_message(), None
