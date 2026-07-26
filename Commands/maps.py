import random
from urllib.parse import urlencode
import webbrowser

from ai.intents import Intent, IntentParameter, IntentRequest


INTENT = Intent(
    name="maps",
    description="Search for a place or get directions using Google Maps.",
    parameters=(
        IntentParameter(
            name="query",
            description="The place, address or destination to search for.",
        ),
    ),
    aliases=(
        "map",
        "directions",
        "navigate",
        "route",
    ),
)


def extract_query_from_command(message: str) -> str:
    """Remove command words to produce a Maps search query."""
    excluded_words = {
        "google",
        "map",
        "maps",
        "search",
        "find",
        "look",
        "up",
        "show",
        "display",
        "navigate",
        "to",
        "directions",
        "route",
        "get",
        "take",
        "me",
        "on",
        "in",
        "near",
        "around",
        "nearby",
        "for",
    }
    query_words = [
        word
        for word in message.split()
        if word.lower() not in excluded_words
    ]

    return " ".join(query_words).strip()


def run(request: IntentRequest) -> bool:
    """Open a Maps search for the location in a structured request."""
    query = extract_query_from_command(request.message)

    if not query:
        print("No valid query found")
        return False

    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    webbrowser.register(
        "chrome",
        None,
        webbrowser.BackgroundBrowser(chrome_path),
    )
    maps_url = "https://www.google.com/maps/search/?" + urlencode(
        {
            "api": 1,
            "query": query,
        }
    )
    webbrowser.get("chrome").open(maps_url, new=2)
    print(random.choice([f"{query} on Google Maps", "Maps loading..."]))
    return True
