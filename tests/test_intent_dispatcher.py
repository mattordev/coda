import unittest

from ai.intents.dispatcher import IntentDispatcher
from ai.intents.models import IntentRequest, IntentResult
from ai.intents.router import IntentRouter
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


if __name__ == "__main__":
    unittest.main()
