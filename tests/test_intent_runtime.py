import unittest
from unittest.mock import Mock, patch

from ai.intents.models import Intent, IntentResult
import utils.on_command as command_runtime


class FakeCommand:
    """Record structured requests and return a configured result."""

    def __init__(self, intent, result=True):
        self.INTENT = intent
        self.result = result
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return self.result


class IntentRuntimeTests(unittest.TestCase):
    @patch.object(command_runtime, "route_request")
    def test_prefix_intent_dispatches_without_llm_fallback(self, route_request):
        command = FakeCommand(
            Intent(
                name="say",
                description="Say something.",
            )
        )
        commands = {"say": command}

        with patch.object(command_runtime, "_intent_registry", None), \
                patch.object(command_runtime, "_intent_router", None):
            command_runtime.configure_intent_router(commands)
            result = command_runtime.on_command(
                "say Hello World",
                commands,
            )

        self.assertTrue(result.handled)
        self.assertEqual(result.command_matches, ("say",))
        self.assertEqual(len(command.requests), 1)
        self.assertEqual(command.requests[0].message, "say Hello World")
        self.assertEqual(command.requests[0].intent.name, "say")
        route_request.assert_not_called()

    @patch.object(command_runtime, "route_request")
    def test_classifier_intent_dispatches_structured_request(self, route_request):
        maps_intent = Intent(
            name="maps",
            description="Open a map.",
        )
        router = Mock()
        router.route.return_value = IntentResult(
            intent=maps_intent,
            confidence=0.9,
            strategy="local_classifier",
            accepted=True,
        )
        command = FakeCommand(maps_intent)

        with patch.object(command_runtime, "_intent_router", router):
            result = command_runtime.on_command(
                "where is the station",
                {"maps": command},
            )

        self.assertTrue(result.handled)
        self.assertEqual(result.command_matches, ("maps",))
        self.assertEqual(command.requests[0].confidence, 0.9)
        self.assertEqual(command.requests[0].strategy, "local_classifier")
        route_request.assert_not_called()

    @patch("utils.speak_response.speak_response")
    @patch.object(
        command_runtime,
        "route_request",
        return_value=("General response", None),
    )
    @patch.object(
        command_runtime,
        "_llm_fallback_enabled",
        return_value=True,
    )
    def test_unmatched_message_uses_llm_fallback(
        self,
        _fallback_enabled,
        route_request,
        speak_response,
    ):
        router = Mock()
        router.route.return_value = IntentResult(intent=None)

        with patch.object(command_runtime, "_intent_router", router):
            result = command_runtime.on_command(
                "tell me something interesting",
                {},
            )

        self.assertTrue(result.handled)
        self.assertTrue(result.used_llm)
        self.assertTrue(result.open_follow_up)
        self.assertEqual(result.response_text, "General response")
        route_request.assert_called_once_with(
            "tell me something interesting"
        )
        speak_response.assert_called_once_with("General response")

    @patch.object(command_runtime, "route_request")
    def test_failed_command_does_not_use_llm_fallback(self, route_request):
        command = FakeCommand(
            Intent(
                name="connected",
                description="Check connectivity.",
            ),
            result=False,
        )
        commands = {"connected": command}

        with patch.object(command_runtime, "_intent_registry", None), \
                patch.object(command_runtime, "_intent_router", None):
            command_runtime.configure_intent_router(commands)
            result = command_runtime.on_command(
                "connected",
                commands,
            )

        self.assertFalse(result.handled)
        self.assertEqual(len(command.requests), 1)
        route_request.assert_not_called()

    @patch.object(command_runtime, "route_request")
    def test_requires_configured_intent_router(self, route_request):
        with patch.object(command_runtime, "_intent_router", None):
            with self.assertRaisesRegex(
                RuntimeError,
                "Intent router has not been configured",
            ):
                command_runtime.on_command("say hello", {})

        route_request.assert_not_called()

    def test_empty_message_does_not_route_or_dispatch(self):
        router = Mock()

        with patch.object(command_runtime, "_intent_router", router):
            result = command_runtime.on_command("   ", {})

        self.assertFalse(result.handled)
        router.route.assert_not_called()


if __name__ == "__main__":
    unittest.main()
