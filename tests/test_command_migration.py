import unittest
from unittest.mock import patch

from ai.intents.defaults import create_builtin_registry
from ai.intents.models import IntentRequest
from commands import debug as debug_command
from commands import maps as maps_command
from commands import say as say_command


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


class SayCommandTests(unittest.TestCase):
    @patch("commands.say.speak.speak_response")
    def test_speaks_content_after_canonical_intent_name(self, speak_response):
        request = _create_request("say", "say Hello World")

        executed = say_command.run(request)

        self.assertTrue(executed)
        speak_response.assert_called_once_with("Hello World")

    def test_speaks_content_after_alias(self):
        cases = (
            ("repeat Mind the gap", "Mind the gap"),
            ("read aloud Platform nine", "Platform nine"),
        )

        for message, expected in cases:
            with self.subTest(message=message):
                request = _create_request("say", message)

                with patch(
                    "commands.say.speak.speak_response"
                ) as speak_response:
                    executed = say_command.run(request)

                self.assertTrue(executed)
                speak_response.assert_called_once_with(expected)

    @patch("commands.say.speak.speak_response")
    def test_reports_failure_when_content_is_missing(self, speak_response):
        request = _create_request("say", "say")

        executed = say_command.run(request)

        self.assertFalse(executed)
        speak_response.assert_called_once_with("Nothing to say.")

    @patch("commands.say.speak.speak_response")
    def test_preserves_message_without_known_trigger(self, speak_response):
        request = _create_request("say", "Please repeat this message")

        executed = say_command.run(request)

        self.assertTrue(executed)
        speak_response.assert_called_once_with("Please repeat this message")


class MapsCommandTests(unittest.TestCase):
    def test_extracts_query_after_command_words(self):
        cases = (
            ("maps London Victoria", "London Victoria"),
            ("map Leeds station", "Leeds station"),
            ("navigate to King's Cross", "King's Cross"),
            ("get directions to York", "York"),
        )

        for message, expected in cases:
            with self.subTest(message=message):
                query = maps_command.extract_query_from_command(message)

                self.assertEqual(query, expected)

    @patch("commands.maps.random.choice", return_value="Maps loading...")
    @patch("commands.maps.webbrowser.get")
    @patch("commands.maps.webbrowser.register")
    def test_opens_selected_location_in_browser(
        self,
        register_browser,
        get_browser,
        _choose_response,
    ):
        request = _create_request("maps", "maps London Victoria")

        executed = maps_command.run(request)

        self.assertTrue(executed)
        register_browser.assert_called_once()
        get_browser.assert_called_once_with("chrome")
        get_browser.return_value.open.assert_called_once_with(
            "https://www.google.com/maps/search/"
            "?api=1&query=London+Victoria",
            new=2,
        )

    @patch("commands.maps.webbrowser.get")
    @patch("builtins.print")
    def test_rejects_request_without_location(self, print_output, get_browser):
        request = _create_request("maps", "maps")

        executed = maps_command.run(request)

        self.assertFalse(executed)
        print_output.assert_called_once_with("No valid query found")
        get_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
