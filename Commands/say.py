from ai.intents import IntentRequest

import utils.speak_response as speak


def _extract_message(request: IntentRequest) -> str:
    """Extract the text following the intent name or an alias."""
    message = request.message.strip()
    normalized_message = message.lower()

    triggers = (
        request.intent.name,
        *request.intent.aliases,
    )

    for trigger in sorted(triggers, key=len, reverse=True):
        normalized_trigger = trigger.lower()

        if normalized_message == normalized_trigger:
            return ""

        prefix = normalized_trigger + " "

        if normalized_message.startswith(prefix):
            return message[len(trigger):].strip()

    return message


def run(request: IntentRequest) -> bool:
    """Speak the message contained in a structured intent request."""
    message = _extract_message(request)

    if not message:
        speak.speak_response("Nothing to say.")
        return False

    speak.speak_response(message)
    return True
