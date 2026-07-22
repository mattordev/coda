import unittest
from unittest.mock import patch

from ai.intents.defaults import create_builtin_registry
from ai.intents.models import IntentRequest
from commands import debug as debug_command


def _create_request(intent_name, message):
    """Create a structured request for a built-in command."""
    registry = create_builtin_registry()

    return IntentRequest(
        intent=registry.get(intent_name),
        message=message,
        confidence=1.0,
        strategy="test",
    )


class DebugCommandTests(unittest.TestCase):
    @patch("commands.debug._print_status")
    @patch("commands.debug.runtime_state.set_debug_enabled")
    def test_enables_debug_mode(self, set_debug_enabled, print_status):
        request = _create_request("debug", "debug on")

        executed = debug_command.run(request)

        self.assertTrue(executed)
        set_debug_enabled.assert_called_once_with(True)
        print_status.assert_called_once_with()

    @patch("commands.debug._print_status")
    @patch("commands.debug.runtime_state.set_debug_enabled")
    def test_disables_debug_mode(self, set_debug_enabled, print_status):
        request = _create_request("debug", "debug mode off")

        executed = debug_command.run(request)

        self.assertTrue(executed)
        set_debug_enabled.assert_called_once_with(False)
        print_status.assert_called_once_with()

    @patch("commands.debug._print_status")
    def test_defaults_to_status_when_action_is_missing(self, print_status):
        request = _create_request("debug", "debug")

        executed = debug_command.run(request)

        self.assertTrue(executed)
        print_status.assert_called_once_with()

    @patch("commands.debug._reload_runtime_config", return_value=True)
    def test_reloads_runtime_configuration(self, reload_runtime_config):
        request = _create_request("debug", "debug config reload")

        executed = debug_command.run(request)

        self.assertTrue(executed)
        reload_runtime_config.assert_called_once_with()

    @patch("builtins.print")
    def test_rejects_unknown_action(self, print_output):
        request = _create_request("debug", "debug explode")

        executed = debug_command.run(request)

        self.assertFalse(executed)
        print_output.assert_called_once_with(
            "Usage: debug [on|off|status|reload]"
        )


if __name__ == "__main__":
    unittest.main()
