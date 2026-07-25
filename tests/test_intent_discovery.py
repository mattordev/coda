import os
from types import ModuleType
import unittest
from unittest.mock import patch

from ai.intents import (
    Intent,
    IntentDispatcher,
    create_command_registry,
    create_router,
)
from commands import connected, debug, maps, say, status


_MISSING = object()


def _command_module(
    module_name,
    intent=_MISSING,
    run=_MISSING,
):
    """Create an in-memory command module for discovery tests."""
    module = ModuleType(module_name)

    if intent is not _MISSING:
        module.INTENT = intent

    if run is not _MISSING:
        module.run = run

    return module


class IntentDiscoveryTests(unittest.TestCase):
    def test_registers_existing_command_modules(self):
        modules = (connected, debug, maps, say, status)
        commands = {
            module.INTENT.name: module
            for module in modules
        }

        registry = create_command_registry(commands)

        self.assertEqual(
            tuple(intent.name for intent in registry.all()),
            ("connected", "debug", "maps", "say", "status"),
        )

    def test_creates_registry_from_command_modules(self):
        maps_intent = Intent(
            name="maps",
            description="Open a map.",
            aliases=("navigate",),
        )
        status_intent = Intent(
            name="status",
            description="Report system status.",
            aliases=("system report",),
        )
        commands = {
            "maps": _command_module("maps", maps_intent, lambda request: True),
            "status": _command_module(
                "status",
                status_intent,
                lambda request: True,
            ),
        }

        registry = create_command_registry(commands)

        self.assertEqual(registry.all(), (maps_intent, status_intent))
        self.assertIs(registry.get("navigate"), maps_intent)
        self.assertIs(registry.get("system report"), status_intent)

    def test_rejects_module_without_intent(self):
        commands = {
            "missing": _command_module("missing", run=lambda request: True),
        }

        with self.assertRaisesRegex(
            TypeError,
            "Command module 'missing' must expose an INTENT",
        ):
            create_command_registry(commands)

    def test_rejects_invalid_intent_metadata(self):
        commands = {
            "invalid": _command_module(
                "invalid",
                intent="not an intent",
                run=lambda request: True,
            ),
        }

        with self.assertRaisesRegex(
            TypeError,
            "Command module 'invalid' must expose an INTENT",
        ):
            create_command_registry(commands)

    def test_rejects_mismatched_module_and_intent_names(self):
        commands = {
            "weather": _command_module(
                "weather",
                intent=Intent("forecast", "Report the weather."),
                run=lambda request: True,
            ),
        }

        with self.assertRaisesRegex(
            ValueError,
            "module name and intent name must match",
        ):
            create_command_registry(commands)

    def test_rejects_module_without_run(self):
        commands = {
            "missing": _command_module(
                "missing",
                Intent("missing", "Missing run function."),
            ),
        }

        with self.assertRaisesRegex(
            TypeError,
            r"Command module 'missing' must expose a callable run\(\)",
        ):
            create_command_registry(commands)

    def test_rejects_non_callable_run(self):
        commands = {
            "invalid": _command_module(
                "invalid",
                Intent("invalid", "Invalid run function."),
                run="not callable",
            ),
        }

        with self.assertRaisesRegex(
            TypeError,
            r"Command module 'invalid' must expose a callable run\(\)",
        ):
            create_command_registry(commands)

    def test_rejects_duplicate_intent_names(self):
        commands = {
            "shared": _command_module(
                "shared",
                Intent("shared", "First command."),
                lambda request: True,
            ),
            " SHARED ": _command_module(
                " SHARED ",
                Intent(" SHARED ", "Second command."),
                lambda request: True,
            ),
        }

        with self.assertRaisesRegex(
            ValueError,
            "Intent 'shared' is already registered",
        ):
            create_command_registry(commands)

    def test_rejects_duplicate_aliases(self):
        commands = {
            "first": _command_module(
                "first",
                Intent("first", "First command.", aliases=("shared",)),
                lambda request: True,
            ),
            "second": _command_module(
                "second",
                Intent("second", "Second command.", aliases=("shared",)),
                lambda request: True,
            ),
        }

        with self.assertRaisesRegex(
            ValueError,
            "Name 'shared' is already registered",
        ):
            create_command_registry(commands)

    def test_discovers_routes_and_dispatches_command(self):
        requests = []

        def run(request):
            requests.append(request)
            return True

        intent = Intent(
            name="greet",
            description="Greet somebody.",
            aliases=("hello",),
        )
        commands = {
            "greet": _command_module("greet", intent, run),
        }
        registry = create_command_registry(commands)

        with patch.dict(
            os.environ,
            {"CODA_INTENT_LOCAL_CLASSIFIER": "0"},
        ):
            router = create_router(registry)

        result = router.route("hello Ada")
        request = result.to_request("hello Ada")
        executed = IntentDispatcher(commands).dispatch(request)

        self.assertTrue(executed)
        self.assertEqual(result.intent, intent)
        self.assertEqual(result.strategy, "command_prefix")
        self.assertEqual(requests, [request])


if __name__ == "__main__":
    unittest.main()
