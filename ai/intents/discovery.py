from collections.abc import Mapping
from types import ModuleType

from .models import Intent
from .registry import IntentRegistry


def create_command_registry(
    commands: Mapping[str, ModuleType],
) -> IntentRegistry:
    """Create an intent registry from loaded command modules."""
    registry = IntentRegistry()

    for module_name, module in commands.items():
        intent = getattr(module, "INTENT", None)
        run = getattr(module, "run", None)

        if not isinstance(intent, Intent):
            raise TypeError(
                f"Command module '{module_name}' must expose an INTENT."
            )

        if not callable(run):
            raise TypeError(
                f"Command module '{module_name}' must expose a callable run()."
            )

        registry.register(intent)

    return registry
