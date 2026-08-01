from ai.intents import Intent, IntentRequest

import utils.speak_response as speak

import datetime


INTENT = Intent(
    name="time",
    description="Ask CODA to report the time or date.",
    aliases=(
        "current time",
        "date",
        "current date",
    ),
    examples=(
        "what's the time",
        "tell me the time",
        "what time is it",
        "what's the time right now",
        "what's the date",
        "what date is it",
        "what's the date right now",
    ),
)

def get_date_time() -> dict[str, str]:
    """Generates the time and dates variables and spoken variants for use elsewhere in the command"""
    current_date_time = datetime.datetime.now()
    formatted_time = current_date_time.strftime("%I:%M %p")
    spoken_time = formatted_time.lstrip("0")
    formatted_date = current_date_time.strftime("%d/%m/%Y")
    spoken_date = format_spoken_date(current_date_time)

    return {
        "time": formatted_time,
        "spoken_time": spoken_time,
        "date": formatted_date,
        "spoken_date": spoken_date,
    }
    
def format_spoken_date(current_date: datetime.datetime) -> str:
    """Formats the time into a spoken state for TTS."""
    return (
        f"the {format_ordinal(current_date.day)} "
        f"of {current_date:%B %Y}"
    )
    
def format_ordinal(number: int) -> str:
    """Formats a given number, adds the ordinal suffix e.g. 1 becomes 1st"""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {
            1: "st",
            2: "nd",
            3: "rd",
        }.get(number % 10, "th")
        
    return f"{number}{suffix}"

def run(request: IntentRequest) -> bool:
    """Return the current time."""
    
    date_and_time = get_date_time()
    
    if ("time" in request.message.lower()):
        print(f"The current time is {date_and_time['time']}.")
        speak.speak_response(f"The time is currently {date_and_time['spoken_time']}.")
    elif("date" in request.message.lower()):
        print(f"The current date is {date_and_time['date']}.")
        speak.speak_response(
            f"The current date is {date_and_time['spoken_date']}."
        )
        
    return True
