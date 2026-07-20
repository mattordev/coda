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
    Intent(
        name="say",
        description="Ask CODA to say something.",
        parameters=(
            IntentParameter(
                name="message",
                description="The thing that you want CODA to say.",
            ),
        ),
        aliases=(
            "repeat",
            "read aloud",
        ),
    ),
    Intent(
        name="debug",
        description="View or change CODA's runtime debugging configuration.",
        parameters=(
            IntentParameter(
                name="action",
                description="The debug action: on, off, status, or reload.",
                required=False,
            ),
        ),
        aliases=(
            "debug mode",
            "diagnostics",
            "diagnostic mode",
        ),
    ),
    Intent(
        name="status",
        description="Report information about the system running CODA.",
        aliases=(
            "system status",
            "system information",
            "system report",
        ),
    ),
)


def create_builtin_registry() -> IntentRegistry:
    """Create a registry populated with CODA's built-in intents."""
    registry = IntentRegistry()

    for intent in BUILTIN_INTENTS:
        registry.register(intent)

    return registry
