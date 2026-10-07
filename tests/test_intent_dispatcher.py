import unittest
from unittest.mock import Mock

from ai.intents.dispatcher import IntentDispatcher
from ai.intents.models import Intent, IntentRequest, IntentResult
from ai.intents.router import IntentRouter
from ai.tools.models import ToolResult
from tests.intent_fixtures import create_test_registry


class FakeIntentCommand:
    """Record structured requests and return a configured result."""

    def __init__(self, result=True):
        self.result = result
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return self.result


class IntentRequestTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_test_registry()
        self.maps_intent = self.registry.get("maps")

    def test_rejects_empty_message(self):
        with self.assertRaisesRegex(ValueError, "message cannot be empty"):
            IntentRequest(
                intent=self.maps_intent,
                message="   ",
                confidence=1.0,
            )

    def test_rejects_confidence_outside_valid_range(self):
        for confidence in (-0.1, 1.1):
            with self.subTest(confidence=confidence):
                with self.assertRaisesRegex(ValueError, "Confidence"):
                    IntentRequest(
                        intent=self.maps_intent,
                        message="find the station",
                        confidence=confidence,
                    )

    def test_accepted_result_creates_structured_request(self):
        result = IntentResult(
            intent=self.maps_intent,
            confidence=0.9,
            strategy="test_strategy",
            accepted=True,
        )

        request = result.to_request("find the station")

        self.assertIs(request.intent, self.maps_intent)
        self.assertEqual(request.message, "find the station")
        self.assertEqual(request.confidence, 0.9)
        self.assertEqual(request.strategy, "test_strategy")

    def test_unaccepted_result_cannot_create_request(self):
        results = (
            IntentResult(intent=None),
            IntentResult(
                intent=self.maps_intent,
                confidence=0.5,
                accepted=False,
            ),
        )

        for result in results:
            with self.subTest(result=result):
                with self.assertRaisesRegex(ValueError, "Only accepted"):
                    result.to_request("find the station")


class IntentDispatcherTests(unittest.TestCase):
    def setUp(self):
        registry = create_test_registry()
        self.maps_request = IntentRequest(
            intent=registry.get("maps"),
            message="find the station",
            confidence=0.9,
            strategy="test_strategy",
        )
        self.status_request = IntentRequest(
            intent=registry.get("status"),
            message="how is the computer",
            confidence=0.9,
            strategy="test_strategy",
        )

    def test_dispatches_structured_request_to_selected_command(self):
        command = FakeIntentCommand()
        dispatcher = IntentDispatcher({"maps": command})

        executed = dispatcher.dispatch(self.maps_request)

        self.assertTrue(executed)
        self.assertEqual(command.requests, [self.maps_request])

    def test_dispatches_intent_selected_by_router(self):
        registry = create_test_registry()
        router = IntentRouter(registry)
        command = FakeIntentCommand()
        dispatcher = IntentDispatcher({"maps": command})

        result = router.route("maps")
        request = result.to_request("maps")
        executed = dispatcher.dispatch(request)

        self.assertTrue(executed)
        self.assertEqual(command.requests, [request])

    def test_returns_false_when_selected_command_is_missing(self):
        command = FakeIntentCommand()
        dispatcher = IntentDispatcher({"maps": command})

        executed = dispatcher.dispatch(self.status_request)

        self.assertFalse(executed)
        self.assertEqual(command.requests, [])

    def test_returns_false_when_command_reports_failure(self):
        command = FakeIntentCommand(result=False)
        dispatcher = IntentDispatcher({"maps": command})

        executed = dispatcher.dispatch(self.maps_request)

        self.assertFalse(executed)
        self.assertEqual(command.requests, [self.maps_request])

    def test_execute_returns_structured_success(self):
        command = FakeIntentCommand()
        dispatcher = IntentDispatcher({"maps": command})

        result = dispatcher.execute(self.maps_request)

        self.assertEqual(result, ToolResult(success=True))
        self.assertEqual(command.requests, [self.maps_request])

    def test_execute_returns_structured_native_failure(self):
        command = FakeIntentCommand(result=False)
        dispatcher = IntentDispatcher({"maps": command})

        result = dispatcher.execute(self.maps_request)

        self.assertEqual(result, ToolResult(success=False))
        self.assertEqual(command.requests, [self.maps_request])

    def test_execute_reports_missing_tool_without_running_another_command(self):
        command = FakeIntentCommand()
        dispatcher = IntentDispatcher({"maps": command})

        result = dispatcher.execute(self.status_request)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "tool_not_found")
        self.assertTrue(result.error.message.strip())
        self.assertEqual(command.requests, [])

    def test_execute_normalizes_command_exception(self):
        command = Mock()
        command.run.side_effect = RuntimeError("private-token-123")
        dispatcher = IntentDispatcher({"maps": command})

        result = dispatcher.execute(self.maps_request)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "execution_error")
        self.assertNotIn("private-token-123", repr(result))
        command.run.assert_called_once_with(self.maps_request)

    def test_dispatch_returns_false_when_command_raises(self):
        command = Mock()
        command.run.side_effect = RuntimeError("Cannot complete.")
        dispatcher = IntentDispatcher({"maps": command})

        self.assertIs(dispatcher.dispatch(self.maps_request), False)
        command.run.assert_called_once_with(self.maps_request)

    def test_routes_and_executes_registered_external_tool_without_mcp(self):
        registry = create_test_registry()
        intent = Intent(name="printer_status", description="Check the printer.")
        registry.register(intent)
        router = IntentRouter(registry)
        expected = ToolResult(success=True, data={"state": "idle"})
        executor = Mock(spec=["execute"])
        executor.execute.return_value = expected
        dispatcher = IntentDispatcher({})
        dispatcher.register(intent.name, executor)
        request = router.route("printer_status").to_request("printer_status")

        result = dispatcher.execute(request)

        self.assertIs(result, expected)
        executor.execute.assert_called_once_with(request)

    def test_external_failure_still_returns_false_from_dispatch(self):
        executor = Mock(spec=["execute"])
        executor.execute.return_value = ToolResult(success=False)
        dispatcher = IntentDispatcher({})
        dispatcher.register("maps", executor)

        self.assertIs(dispatcher.dispatch(self.maps_request), False)
        executor.execute.assert_called_once_with(self.maps_request)

    def test_duplicate_registration_preserves_original_native_executor(self):
        command = FakeIntentCommand()
        dispatcher = IntentDispatcher({"maps": command})
        replacement = Mock(spec=["execute"])

        with self.assertRaisesRegex(ValueError, "already registered"):
            dispatcher.register("maps", replacement)

        self.assertTrue(dispatcher.execute(self.maps_request).success)
        self.assertEqual(command.requests, [self.maps_request])
        replacement.execute.assert_not_called()

    def test_registration_rejects_blank_names(self):
        dispatcher = IntentDispatcher({})
        executor = Mock(spec=["execute"])

        for name in ("", " ", "\t\n"):
            with self.subTest(name=repr(name)):
                with self.assertRaisesRegex(ValueError, "name cannot be empty"):
                    dispatcher.register(name, executor)

    def test_native_registration_uses_the_same_name_validation(self):
        with self.assertRaisesRegex(ValueError, "name cannot be empty"):
            IntentDispatcher({" ": FakeIntentCommand()})

    def test_registration_rejects_non_string_names(self):
        dispatcher = IntentDispatcher({})
        executor = Mock(spec=["execute"])

        for name in (None, 12, False):
            with self.subTest(name=name):
                with self.assertRaisesRegex(TypeError, "must be a string"):
                    dispatcher.register(name, executor)

    def test_invalid_executor_does_not_reserve_the_name(self):
        dispatcher = IntentDispatcher({})

        for executor in (None, object(), Mock(execute=False)):
            with self.subTest(executor=executor):
                with self.assertRaisesRegex(TypeError, "callable execute"):
                    dispatcher.register("maps", executor)

        valid_executor = Mock(spec=["execute"])
        valid_executor.execute.return_value = ToolResult(success=True)
        dispatcher.register("maps", valid_executor)
        self.assertTrue(dispatcher.execute(self.maps_request).success)

    def test_external_exception_is_safe_and_does_not_invoke_other_tools(self):
        executor = Mock(spec=["execute"])
        executor.execute.side_effect = RuntimeError("private-token-123")
        other_command = FakeIntentCommand()
        dispatcher = IntentDispatcher({"status": other_command})
        dispatcher.register("maps", executor)

        result = dispatcher.execute(self.maps_request)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "execution_error")
        self.assertNotIn("private-token-123", repr(result))
        executor.execute.assert_called_once_with(self.maps_request)
        self.assertEqual(other_command.requests, [])

    def test_invalid_external_results_become_structured_failures(self):
        for value in (True, False, None, {"success": True}, "private-token-123"):
            with self.subTest(value=value):
                executor = Mock(spec=["execute"])
                executor.execute.return_value = value
                dispatcher = IntentDispatcher({})
                dispatcher.register("maps", executor)

                result = dispatcher.execute(self.maps_request)

                self.assertFalse(result.success)
                self.assertEqual(result.error.code, "invalid_tool_result")
                self.assertNotIn("private-token-123", repr(result))
                self.assertIs(dispatcher.dispatch(self.maps_request), False)

    def test_external_keyboard_interrupt_propagates(self):
        executor = Mock(spec=["execute"])
        executor.execute.side_effect = KeyboardInterrupt()
        dispatcher = IntentDispatcher({})
        dispatcher.register("maps", executor)

        with self.assertRaises(KeyboardInterrupt):
            dispatcher.execute(self.maps_request)


if __name__ == "__main__":
    unittest.main()
