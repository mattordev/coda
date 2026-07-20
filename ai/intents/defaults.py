from .models import Intent, IntentParameter
from .registry import IntentRegistry

BUILTIN_INTENTS: tuple[Intent, ...] = (
    Intent(
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
    ),
    Intent(
        name="connected",
        description="Checks if CODA can reach the internet.",
        aliases=(
            "connection",
            "connectivity",
            "online",
            "offline",
            "internet status",
            "network status",
        ),
    ),
)

def create_builtin_registry() -> IntentRegistry:
    """Create a registry populated with CODA's built-in intents."""
    registry = IntentRegistry()
    
    for intent in BUILTIN_INTENTS:
        registry.register(intent)
        
    return registry