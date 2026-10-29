"""Generation controls stay request-scoped and do not replay provider calls."""

from dataclasses import FrozenInstanceError
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai.providers.generation import GenerationOptions, Workload, call_generation
from ai.providers.telemetry import ProviderCallResult
from ai.telemetry.models import UsageMetrics
from ai.llm_router import core as router
import utils.llm_service as service


class GenerationOptionsTests(unittest.TestCase):
    def test_inheritance_validation_and_immutability(self):
        default = GenerationOptions()
        self.assertIsNone(default.thinking)
        self.assertIsNone(default.max_tokens)
        with self.assertRaises(FrozenInstanceError):
            default.thinking = False
        for kwargs in ({"workload": "conversation"}, {"thinking": 1},
                       {"thinking": "false"}, {"max_tokens": True},
                       {"max_tokens": 0}, {"max_tokens": -1}, {"max_tokens": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                GenerationOptions(**kwargs)

    def test_omitted_options_keep_the_existing_call_shape(self):
        generate = Mock(return_value=("answer", None))
        messages = [{"role": "user", "content": "hello"}]
        self.assertEqual(call_generation(generate, messages), ("answer", None))
        generate.assert_called_once_with(messages, cancel_event=None)

    def test_options_reach_capable_provider_and_legacy_provider_is_called_once(self):
        options = GenerationOptions(Workload.INTERPRETATION, False, 512)
        cancel = Event()
        calls = []

        def capable(messages, cancel_event=None, *, options=None):
            calls.append((messages, cancel_event, options))
            return "answer", None

        self.assertEqual(call_generation(capable, [], cancel_event=cancel, options=options), ("answer", None))
        self.assertIs(calls[0][1], cancel)
        self.assertIs(calls[0][2], options)

        def legacy(messages, cancel_event=None):
            calls.append((messages, cancel_event))
            return "legacy", None

        self.assertEqual(call_generation(legacy, [], cancel_event=cancel, options=options), ("legacy", None))
        self.assertEqual(len(calls), 2)

    def test_provider_type_error_is_not_retried(self):
        generate = Mock(side_effect=TypeError("failure after accepting request"))
        with self.assertRaisesRegex(TypeError, "after accepting"):
            call_generation(generate, [], options=GenerationOptions())
        self.assertEqual(generate.call_count, 1)

    def test_stateless_options_preserve_metadata_and_cancellation(self):
        options = GenerationOptions(Workload.INTERPRETATION, False, 512)
        result = ProviderCallResult("answer", None, "fixture", UsageMetrics(output_tokens=7))
        generate = Mock(return_value=result)
        with patch.object(service.registry, "get_provider_module", return_value=SimpleNamespace(
            generate_with_metadata=generate,
        )):
            self.assertEqual(service.call_with_messages("fixture", [], options=options), result)
            self.assertIs(generate.call_args.kwargs["options"], options)
            cancel = Event()
            cancel.set()
            self.assertEqual(service.call_with_messages("fixture", [], options=options,
                                                        cancel_event=cancel), (None, "Request cancelled."))
        self.assertEqual(generate.call_count, 1)

    def test_options_reach_each_fallback_without_changing_privacy_routing(self):
        options = GenerationOptions(thinking=False, max_tokens=128)
        cancel = Event()
        with patch.object(router, "get_provider_order", return_value=["first", "second"]), \
             patch.object(router, "_call_provider_with_health", side_effect=[
                 (None, "fixture failure", False), ("answer", None, False),
             ]) as call, patch.object(router.logger, "log_failure"):
            self.assertEqual(router.route_request("hello", cancel_event=cancel, options=options), ("answer", None))
        self.assertEqual(call.call_count, 2)
        for attempt in call.call_args_list:
            self.assertIs(attempt.kwargs["options"], options)
            self.assertIs(attempt.kwargs["cancel_event"], cancel)

    def test_conversation_failure_rolls_back_history_with_options(self):
        options = GenerationOptions(thinking=False)
        generate = Mock(return_value=(None, "fixture failure"))
        history = [service._new_system_message()]
        with patch.object(service, "conversation_log", history.copy()), \
             patch.object(service.registry, "get_provider_type", return_value="local"):
            result = service._generate_provider_response("fixture", SimpleNamespace(
                generate_with_metadata=generate,
            ), "hello", options=options)
            self.assertEqual(result, (None, "fixture failure"))
            self.assertEqual(service.conversation_log, history)
            self.assertIs(generate.call_args.kwargs["options"], options)


if __name__ == "__main__":
    unittest.main()
