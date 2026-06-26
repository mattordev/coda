import utils.llm_service as llm_service
from ai.privacy.detector import detect_privacy
from ai.telemetry import logger
import utils.runtime_state as runtime_state
from providers.registry import is_provider_supported, is_provider_configured


def _debug_print(*args, **kwargs):
    runtime_state.debug_print(*args, **kwargs)


def _call_provider_with_health(provider: str, prompt: str):
    if logger.should_skip_provider(provider):
        print(f"[ROUTER] Skipping {provider} due to recent failure; using fallback.")
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

def get_provider_order(risk: float):

    cloud = llm_service.get_llm_provider()

    if risk > 0.7:
        return ["ollama"]

    if risk > 0.3:
        return ["ollama", cloud]

    return [cloud, "ollama"]

def route_request(prompt: str):
    risk = detect_privacy(prompt)
    

    providers = get_provider_order(risk)

    _debug_print(f"[DEBUG - ROUTER] Privacy risk: {risk}")
    _debug_print(f"[DEBUG - ROUTER] Provider order: {providers}")

    # High risk, LOCAL ONLY
    if risk > 0.7:
        provider = providers[0]
        
        print(f"[ROUTER] Using {provider} due to high privacy risk")

        response, error, skipped = _call_provider_with_health(provider, prompt)

        if skipped or error or not response:
            if not skipped:
                logger.log_failure(provider)
                _debug_print(f"[ROUTER] {provider} failed: {error}")
            return (
                _high_risk_unavailable_message(),
                None,
            )

        return response, None

    # Medium risk, try LOCAL first
    if risk > 0.3:
        
        local_provider = providers[0] 
        cloud_provider = providers[1] 
        
        print(f"[ROUTER] Medium risk → trying LOCAL ({local_provider}) first")

        response, error, skipped = _call_provider_with_health(local_provider, prompt)

        if not skipped and not error and response:
            return response, None

        if not skipped:
            logger.log_failure(local_provider)
            _debug_print(f"[ROUTER] {local_provider} failed: {error}")
        else:
            print(f"[ROUTER] {local_provider} skipped due to recent failure; trying CLOUD ({cloud_provider}) instead")

        print(f"[ROUTER] Local failed → trying CLOUD ({cloud_provider})")
        
        response, error, skipped = _call_provider_with_health(cloud_provider, prompt)

        if skipped or error or not response:
            if not skipped:
                logger.log_failure(cloud_provider)
                _debug_print(f"[ROUTER] {cloud_provider} failed: {error}")
            print("[ROUTER] Cloud also failed → giving up")
            return (
                _provider_unavailable_message(),
                None,
            )

        return response, None

    # Low risk, CLOUD first, fallback to LOCAL
    provider = providers[0]
    fallback_provider = providers[1]
    
    _debug_print(f"[DEBUG] Provider being checked: {provider}")

    print(f"[ROUTER] → Calling {provider} first (low privacy risk)")

    response, error, skipped = _call_provider_with_health(provider, prompt)

    if skipped:
        
        print(f"[ROUTER] {provider} skipped due to recent failure; trying {fallback_provider} instead")

        response, error, skipped = _call_provider_with_health(fallback_provider, prompt)

        if skipped or error or not response:
            if not skipped:
                logger.log_failure(fallback_provider)
                print(f"[ROUTER] {fallback_provider} failed: {error}")
            return (
                _provider_unavailable_message(),
                None,
            )

        return response, None

    if error or not response:
        logger.log_failure(provider)
        _debug_print(f"[ROUTER] {provider} failed: {error}")

        print(
        f"[ROUTER] Primary cloud provider ({provider}) failed → "
        f"falling back to local provider ({fallback_provider})"
        )

        fallback_response, fallback_error, fallback_skipped = _call_provider_with_health(fallback_provider, prompt)

        if fallback_skipped or fallback_error or not fallback_response:
            if not fallback_skipped:
                logger.log_failure(fallback_provider)
                _debug_print(f"[ROUTER] {fallback_provider} failed: {fallback_error}")
            return (
                _provider_unavailable_message(),
                None,
            )

        return fallback_response, None

    return response, None