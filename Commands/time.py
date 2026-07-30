from ai.intents import Intent, IntentParameter, IntentRequest

import utils.speak_response as speak


INTENT = Intent(
    name="time",
    description="Ask CODA to report the time",
    aliases=(
        "time",
        "current time",
        "what's the time",
        "tell me the time",
        "what time is it",
        "what's the time right now",
    ),
)

def run(request: IntentRequest) -> bool:
    """Return the current time."""
    return True
