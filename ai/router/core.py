import utils.llm_service as llm_service
from ai.privacy.detector import detect_privacy
from ai.telemetry import logger
import utils.runtime_state as runtime_state


def _debug_print(*args, **kwargs):
    runtime_state.debug_print(*args, **kwargs)


def route_request(prompt: str):
    risk = detect_privacy(prompt)

    _debug_print(f"[DEBUG - ROUTER] Privacy risk: {risk}")

    # High risk, LOCAL ONLY
    if risk > 0.7:
        print("[ROUTER] Using LOCAL model (ollama) due to high privacy risk")

        response, error = llm_service.call_provider("ollama", prompt)

        if error or not response:
            logger.log_failure("ollama")
            print(f"[ROUTER] ollama failed: {error}")
            return (
                "I'm unable to process that request locally right now and cannot process it online due to privacy concerns.",
                error,
            )

        return response, None

    # Medium risk, try LOCAL first
    if risk > 0.3:
        print("[ROUTER] Medium risk → trying LOCAL (ollama) first")

        response, error = llm_service.call_provider("ollama", prompt)

        if not error and response:
            return response, None

        logger.log_failure("ollama")
        print(f"[ROUTER] ollama failed: {error}")

        print("[ROUTER] Local failed → trying CLOUD (openai)")

        provider = llm_service.get_llm_provider()
        
        logger.log_attempt(provider) # log attempt BEFORE calling
        
        response, error = llm_service._generate_llm_response(prompt)

        if error or not response:
            logger.log_failure(provider)
            print(f"[ROUTER] {provider} failed: {error}")
            print("[ROUTER] Cloud also failed → giving up")
            return (
                "I'm unable to process that request locally right now and cannot process it online due to privacy concerns.",
                error,
            )

        return response, None

    # Low risk, CLOUD first, fallback to LOCAL
    provider = llm_service.get_llm_provider()
    
    _debug_print(f"[DEBUG] Provider being checked: {provider}")

    #  Skip provider if it recently failed or was just attempted
    if logger.should_skip_provider(provider):
        _debug_print(f"[ROUTER] Skipping {provider} (recent failure)")

        response, error = llm_service.call_provider("ollama", prompt)

        if error or not response:
            logger.log_failure("ollama")
            print(f"[ROUTER] ollama failed: {error}")
            return (
                "I'm unable to process that request locally right now and cannot process it online due to privacy concerns.",
                error,
            )

        return response, None

    print(f"[ROUTER] → Calling {llm_service.describe_llm_fallback()}")

    # 🔥 Track attempt BEFORE calling
    logger.log_attempt(provider)

    response, error = llm_service._generate_llm_response(prompt)

    if error or not response:
        logger.log_failure(provider)
        print(f"[ROUTER] {provider} failed: {error}")

        print("[ROUTER] Cloud failed → falling back to LOCAL (ollama)")

        fallback_response, fallback_error = llm_service.call_provider("ollama", prompt)

        if fallback_error or not fallback_response:
            logger.log_failure("ollama")
            print(f"[ROUTER] ollama failed: {fallback_error}")
            return (
                "I'm unable to process that request locally right now and cannot process it online due to privacy concerns.",
                fallback_error,
            )

        return fallback_response, None

    return response, None