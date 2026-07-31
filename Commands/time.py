from ai.intents import Intent, IntentParameter, IntentRequest

import utils.speak_response as speak

import datetime


INTENT = Intent(
    name="time",
    description="Ask CODA to report the time or date.",
    aliases=(
        "current time",
        "what's the time",
        "tell me the time",
        "what time is it",
        "what's the time right now",
        "current date",
        "what's the date",
        "what date is it",
        "what's the date right now"
    ),
)

def get_date_time() -> str:
    formatted_time = datetime.datetime.now().strftime("%H:%M:%p")
    formatted_date = datetime.datetime.now().strftime("%d/%m/%Y")
    return {
        "time": formatted_time,
        "date": formatted_date
    }

def run(request: IntentRequest) -> bool:
    """Return the current time."""
    
    if ("time" in request.message):
        print(f"The current time is {get_date_time["time"]}.")
        speak.speak_response(f"The time is currently {get_date_time["time"]}.")
    elif("date" in request.message):
        print(f"The current date is {get_date_time["date"]}.")
        speak.speak_response(f"The current date is {get_date_time["date"]}.")
        
    return True
