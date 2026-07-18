import os

from ai.providers import registry
from ai.privacy.detector import detect_privacy
from ai.telemetry import logger
import utils.llm_service as llm_service
import utils.runtime_state as runtime_state


def _debug_print(*args, **kwargs):
    runtime_state.debug_print(*args, **kwargs)


def _try_providers(providers: list[str], prompt: str):
    """
    Attempt providers in order and return the first successful response.
    """
    last_error = None

    for provider in providers:
        _debug_print(f"[ROUTER] trying provider {provider}")

        response, error, skipped = _call_provider_with_health(provider, prompt)

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


def _call_provider_with_health(provider: str, prompt: str):
    if logger.should_skip_provider(provider):
        _debug_print(
            f"[ROUTER] Skipping {provider} due to recent failure; using fallback."
        )
        return None, f"{provider} is temporarily unavailable after a recent failure.", True

    logger.log_attempt(provider)
    response, error = llm_service.call_provider(provider, prompt)
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


def get_provider_order(risk: float):
    cloud_providers, local_providers = _get_configured_provider_groups()

    if risk > 0.7:
        return local_providers

    if risk > 0.3:
        return local_providers + cloud_providers

    return cloud_providers + local_providers


def route_request(prompt: str):
    risk = detect_privacy(prompt)

    providers = get_provider_order(risk)

    _debug_print(f"[DEBUG - ROUTER] Privacy risk: {risk}")
    _debug_print(f"[DEBUG - ROUTER] Provider order: {providers}")

    # High risk, LOCAL ONLY
    if risk > 0.7:
        if not providers:
            _debug_print("[ROUTER] High risk -> no local providers configured.")
            return _no_local_provider_configured_message(), None

        _debug_print("[ROUTER] High risk -> using LOCAL providers only")

        response, error = _try_providers(providers, prompt)

        if response:
            return response, None

        return _high_risk_unavailable_message(), None

    # Medium risk, try LOCAL first
    if risk > 0.3:
        if not providers:
            _debug_print("[ROUTER] Medium risk -> no providers configured.")
            return _no_provider_configured_message(), None

        _debug_print(f"[ROUTER] Medium risk -> trying providers in order: {providers}")

        response, error = _try_providers(providers, prompt)

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

    response, error = _try_providers(providers, prompt)

    if response:
        return response, None

    _debug_print("[ROUTER] All providers failed -> giving up")
    return _provider_unavailable_message(), None
