import utils.llm_service as llm_service
from ai.privacy.detector import detect_privacy



def route_request(prompt: str):
    risk = detect_privacy(prompt)

    print(f"[DEBUG - ROUTER] Privacy risk: {risk}")

    # High risk, LOCAL ONLY
    if risk > 0.7:
        print("[ROUTER] Using LOCAL model (ollama) due to high privacy risk")

        response, error = llm_service._generate_local_response(prompt)

        if error or not response:
            return (
                "I'm unable to process that request locally right now and cannot process it online due to privacy concerns.",
                error,
            )

        return response, None

    # Medium risk, try LOCAL first
    if risk > 0.3:
        print("[ROUTER] Medium risk → trying LOCAL (ollama) first")

        response, error = llm_service._generate_local_response(prompt)

        if not error and response:
            return response, None

        print("[ROUTER] Local failed → trying CLOUD (openai)")

        response, error = llm_service._generate_llm_response(prompt)

        if error or not response:
            print("[ROUTER] Cloud also failed → giving up")
            return (
                "I'm unable to process that request locally right now and cannot process it online due to privacy concerns.",
                error,
            )

        return response, None

    # Low risk, CLOUD first, fallback to LOCAL
    print(f"[ROUTER] → Calling {llm_service.describe_llm_fallback()}")

    response, error = llm_service._generate_llm_response(prompt)

    if error or not response:
        print("[ROUTER] Cloud failed → falling back to LOCAL (ollama)")

        fallback_response, fallback_error = llm_service._generate_local_response(prompt)

        if not fallback_error and fallback_response:
            return fallback_response, None

        return (
            "I'm unable to process that request locally right now and cannot process it online due to privacy concerns.",
            error or fallback_error,
        )

    return response, None