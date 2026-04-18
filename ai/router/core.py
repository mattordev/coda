import utils.llm_service as llm_service
from ai.privacy.detector import detect_privacy


def route_request(prompt: str):
    risk = detect_privacy(prompt)

    # Debug
    print(f"[DEBUG - ROUTER] Privacy risk: {risk}")

    # 🔒 High risk → force local ONLY
    if risk > 0.7:
        response, error = llm_service.generate_local_response(prompt)

        if error:
            return (
                "I'm unable to process that request locally right now. "
                "Please try again in a moment.",
                error,
            )

        return response, None

    # 🌐 Low risk → normal provider logic
    return llm_service.generate_llm_response(prompt)