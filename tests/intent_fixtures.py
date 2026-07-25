from ai.intents import Intent, IntentRegistry


TEST_INTENTS = (
    Intent(
        name="maps",
        description="Open a map.",
        aliases=("map", "navigate"),
        examples=("Where is the station?",),
    ),
    Intent(
        name="connected",
        description="Check connectivity.",
        aliases=("online",),
    ),
    Intent(
        name="say",
        description="Say something.",
        aliases=("repeat", "read aloud"),
    ),
    Intent(
        name="debug",
        description="Configure debugging.",
    ),
    Intent(
        name="status",
        description="Report system status.",
    ),
)


def create_test_registry():
    """Create a fresh registry for intent unit tests."""
    registry = IntentRegistry()

    for intent in TEST_INTENTS:
        registry.register(intent)

    return registry
