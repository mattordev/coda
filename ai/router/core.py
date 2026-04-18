import utils.llm_service as llm_service
from ai.privacy.detector import detect_privacy


def route_request(prompt: str):
    risk = detect_privacy(prompt)

    # Debug
    print(f"[DEBUG - ROUTER] Privacy risk: {risk}")

    # High risk, force local ONLY
    if risk > 0.7:
        print("[ROUTER] Using LOCAL model (privacy)")

        response, error = llm_service.generate_local_response(prompt)

        if error or not response:
            return (
                "I'm unable to process that request locally right now. "
                "Please try again in a moment.",
                error,
            )

        return response, None

    # Medium risk, prefer local, fallback allowed
    if risk > 0.3:
        print("[ROUTER] Medium risk → trying LOCAL first")

        response, error = llm_service.generate_local_response(prompt)

        if not error and response:
            return response, None

        print("[ROUTER] Local failed → falling back to DEFAULT provider")
        return llm_service.generate_llm_response(prompt)

    # Low risk, normal provider logic
    print("[ROUTER] Using DEFAULT provider")
    return llm_service.generate_llm_response(prompt)