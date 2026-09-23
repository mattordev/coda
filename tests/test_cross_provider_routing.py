import unittest
from threading import Event
from types import SimpleNamespace
from unittest import mock

from ai.llm_router import core
from ai.providers.telemetry import ProviderCallResult
from ai.telemetry.models import (
    AttemptOutcome,
    PrivacyAction,
    ProviderLocality,
    UsageMetrics,
    WorkloadPurpose,
)
import utils.llm_service as llm_service


class CrossProviderRoutingTests(unittest.TestCase):
    def tearDown(self):
        llm_service._reset_conversation()

    def test_ordered_fallback_across_multiple_cloud_providers(self):
        attempted = []

        def fake_call_provider(provider, *_args, **_kwargs):
            attempted.append(provider)

            if provider in ("openrouter", "grok"):
                return None, f"{provider} failed"

            return "gemini response", None

        with mock.patch.object(
            core,
            "get_provider_order",
            return_value=[
                "openrouter",
                "grok",
                "gemini",
                "llamacpp",
            ],
        ), mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(
            core.logger,
            "log_attempt",
        ), mock.patch.object(
            core.logger,
            "log_failure",
        ), mock.patch.object(
            core.llm_service,
            "call_provider",
            side_effect=fake_call_provider,
        ):
            response, error = core.route_request(
                "what is the capital of france"
            )

        self.assertEqual(
            (response, error),
            ("gemini response", None),
        )

        self.assertEqual(
            attempted,
            [
                "openrouter",
                "grok",
                "gemini",
            ],
        )

    def test_successful_conversation_attempt_is_recorded(self):
        """A conversation call emits one privacy-safe attempt record."""
        metrics = UsageMetrics(
            total_duration_seconds=1.25,
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        )
        provider_result = ProviderCallResult(
            response="hello",
            error=None,
            model="resolved-model",
            metrics=metrics,
        )

        with mock.patch.object(
            core,
            "get_provider_order",
            return_value=["openrouter"],
        ), mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(
            core.logger,
            "log_attempt",
        ), mock.patch.object(
            core.logger,
            "record_attempt",
        ) as record_attempt, mock.patch.object(
            core.llm_service,
            "call_provider",
            return_value=provider_result,
        ):
            response, error = core.route_request("hello")

        self.assertEqual((response, error), ("hello", None))
        record_attempt.assert_called_once()
        attempt = record_attempt.call_args.args[0]
        self.assertTrue(attempt.trace_id)
        self.assertEqual(attempt.sequence, 1)
        self.assertEqual(attempt.purpose, WorkloadPurpose.CONVERSATION)
        self.assertEqual(attempt.provider, "openrouter")
        self.assertEqual(attempt.model, "resolved-model")
        self.assertEqual(attempt.locality, ProviderLocality.CLOUD)
        self.assertEqual(attempt.outcome, AttemptOutcome.SUCCESS)
        self.assertEqual(attempt.privacy_action, PrivacyAction.RAW)
        self.assertFalse(attempt.is_retry)
        self.assertFalse(attempt.is_fallback)
        self.assertEqual(attempt.metrics, metrics)

    def test_failure_empty_and_cooldown_outcomes_are_distinct(self):
        cases = (
            (
                "failure",
                False,
                ProviderCallResult(
                    None,
                    "provider failed",
                    "model-a",
                    UsageMetrics(total_duration_seconds=0.5),
                ),
                AttemptOutcome.FAILURE,
            ),
            (
                "empty",
                False,
                ProviderCallResult(
                    "",
                    None,
                    "model-a",
                    UsageMetrics(total_duration_seconds=0.5),
                ),
                AttemptOutcome.EMPTY_RESPONSE,
            ),
            (
                "cooldown",
                True,
                None,
                AttemptOutcome.COOLDOWN_SKIPPED,
            ),
        )

        for name, should_skip, result, expected_outcome in cases:
            with self.subTest(name=name), mock.patch.object(
                core.logger,
                "should_skip_provider",
                return_value=should_skip,
            ), mock.patch.object(
                core.logger,
                "log_attempt",
            ), mock.patch.object(
                core.logger,
                "record_attempt",
            ) as record_attempt, mock.patch.object(
                core.llm_service,
                "call_provider",
                return_value=result,
            ) as call_provider:
                core._call_provider_with_health(
                    "openrouter",
                    "hello",
                    0.0,
                    trace_id="trace-1",
                )

            attempt = record_attempt.call_args.args[0]
            self.assertEqual(attempt.outcome, expected_outcome)
            if should_skip:
                call_provider.assert_not_called()
                self.assertEqual(attempt.metrics, UsageMetrics())
            else:
                self.assertEqual(attempt.model, "model-a")

    def test_cancellation_is_recorded_without_reliability_penalty(self):
        cancel_event = Event()

        def cancel_provider(*_args, **_kwargs):
            cancel_event.set()
            return ProviderCallResult(
                None,
                "Request cancelled.",
                "model-a",
                UsageMetrics(total_duration_seconds=0.25),
            )

        with mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(
            core.logger,
            "log_attempt",
        ), mock.patch.object(
            core.logger,
            "log_failure",
        ) as log_failure, mock.patch.object(
            core.logger,
            "record_attempt",
        ) as record_attempt, mock.patch.object(
            core.llm_service,
            "call_provider",
            side_effect=cancel_provider,
        ):
            response, error = core._try_providers(
                ["openrouter", "ollama"],
                "hello",
                0.0,
                cancel_event=cancel_event,
            )

        self.assertEqual((response, error), (None, "Request cancelled."))
        log_failure.assert_not_called()
        attempt = record_attempt.call_args.args[0]
        self.assertEqual(attempt.outcome, AttemptOutcome.CANCELLED)
        self.assertEqual(attempt.sequence, 1)

    def test_trace_correlates_fallback_and_retry_attempts(self):
        results = (
            (None, "first failure"),
            (None, "fallback failure"),
            ("retry success", None),
        )

        with mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(
            core.logger,
            "log_attempt",
        ), mock.patch.object(
            core.logger,
            "log_failure",
        ), mock.patch.object(
            core.logger,
            "record_attempt",
        ) as record_attempt, mock.patch.object(
            core.llm_service,
            "call_provider",
            side_effect=results,
        ):
            response, error = core._try_providers(
                ["openrouter", "ollama", "openrouter"],
                "hello",
                0.0,
            )

        self.assertEqual((response, error), ("retry success", None))
        attempts = [call.args[0] for call in record_attempt.call_args_list]
        self.assertEqual([attempt.sequence for attempt in attempts], [1, 2, 3])
        self.assertEqual(len({attempt.trace_id for attempt in attempts}), 1)
        self.assertFalse(attempts[0].is_fallback)
        self.assertTrue(attempts[1].is_fallback)
        self.assertTrue(attempts[2].is_retry)
        self.assertFalse(attempts[2].is_fallback)

    def test_ordered_fallback_across_multiple_local_providers(self):
        attempted = []

        def fake_call_provider(provider, *_args, **_kwargs):
            attempted.append(provider)

            if provider == "ollama":
                return None, "ollama failed"

            return "llamacpp response", None

        privacy_result = {
            "risk": 1.0,
            "level": "high",
            "categories": ["credential"],
            "matches": [],
        }

        with mock.patch.object(
            core,
            "analyze_privacy",
            return_value=privacy_result,
        ), mock.patch.object(
            core,
            "get_provider_order",
            return_value=[
                "ollama",
                "llamacpp",
            ],
        ), mock.patch.object(
            core.policy,
            "get_cloud_action",
            return_value=core.policy.ACTION_BLOCK,
        ), mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(
            core.logger,
            "log_attempt",
        ), mock.patch.object(
            core.logger,
            "log_failure",
        ), mock.patch.object(
            core.llm_service,
            "call_provider",
            side_effect=fake_call_provider,
        ):
            response, error = core.route_request(
                "my password is secret"
            )

        self.assertEqual(
            (response, error),
            ("llamacpp response", None),
        )

        self.assertEqual(
            attempted,
            [
                "ollama",
                "llamacpp",
            ],
        )

    def test_cloud_to_local_fallback(self):
        attempted = []

        def fake_call_provider(provider, *_args, **_kwargs):
            attempted.append(provider)

            if provider in (
                "openrouter",
                "gemini",
                "openai",
            ):
                return None, f"{provider} failed"

            return "local response", None

        with mock.patch.object(
            core,
            "get_provider_order",
            return_value=[
                "openrouter",
                "gemini",
                "openai",
                "llamacpp",
            ],
        ), mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(
            core.logger,
            "log_attempt",
        ), mock.patch.object(
            core.logger,
            "log_failure",
        ), mock.patch.object(
            core.llm_service,
            "call_provider",
            side_effect=fake_call_provider,
        ):
            response, error = core.route_request(
                "tell me a joke"
            )

        self.assertEqual(
            (response, error),
            ("local response", None),
        )

        self.assertEqual(
            attempted,
            [
                "openrouter",
                "gemini",
                "openai",
                "llamacpp",
            ],
        )

    def test_high_risk_request_does_not_reach_cloud_provider(self):
        attempted = []

        def fake_call_provider(provider, *_args, **_kwargs):
            attempted.append(provider)
            return "local response", None

        privacy_result = {
            "risk": 1.0,
            "level": "high",
            "categories": ["credential"],
            "matches": [],
        }

        with mock.patch.object(
            core,
            "analyze_privacy",
            return_value=privacy_result,
        ), mock.patch.object(
            core,
            "get_provider_order",
            return_value=[
                "ollama",
                "llamacpp",
            ],
        ), mock.patch.object(
            core.policy,
            "get_cloud_action",
            return_value=core.policy.ACTION_BLOCK,
        ), mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(
            core.logger,
            "log_attempt",
        ), mock.patch.object(
            core.llm_service,
            "call_provider",
            side_effect=fake_call_provider,
        ):
            response, error = core.route_request(
                "my password is secret"
            )

        self.assertEqual(
            (response, error),
            ("local response", None),
        )

        self.assertEqual(
            attempted,
            ["ollama"],
        )

        self.assertFalse(
            any(
                provider in {
                    "openrouter",
                    "grok",
                    "gemini",
                    "openai",
                }
                for provider in attempted
            )
        )

    def test_cancellation_prevents_further_provider_fallback(self):
        cancel_event = Event()
        attempted = []

        def fake_call_provider(provider, *_args, **_kwargs):
            attempted.append(provider)
            cancel_event.set()
            return None, "provider failed"

        with mock.patch.object(
            core,
            "get_provider_order",
            return_value=[
                "openrouter",
                "grok",
                "gemini",
                "llamacpp",
            ],
        ), mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(
            core.logger,
            "log_attempt",
        ), mock.patch.object(
            core.logger,
            "log_failure",
        ), mock.patch.object(
            core.llm_service,
            "call_provider",
            side_effect=fake_call_provider,
        ):
            response, error = core.route_request(
                "cancel me",
                cancel_event=cancel_event,
            )

        self.assertIsNone(response)
        self.assertEqual(
            error,
            "Request cancelled.",
        )

        self.assertEqual(
            attempted,
            ["openrouter"],
        )

    def test_provider_failure_does_not_corrupt_conversation_history(self):
        original_history = list(
            llm_service.conversation_log
        )

        provider = SimpleNamespace(
            generate=mock.Mock(return_value=(
                None,
                "provider failed",
            )),
        )

        with mock.patch.object(
            llm_service.registry,
            "get_provider_module",
            return_value=provider,
        ), mock.patch.object(
            llm_service.registry,
            "normalize_provider_name",
            return_value="openrouter",
        ), mock.patch.object(
            llm_service.registry,
            "get_provider_type",
            return_value="cloud",
        ):
            response, error = llm_service.call_provider(
                "openrouter",
                "hello",
            )

        self.assertIsNone(response)
        self.assertEqual(
            error,
            "provider failed",
        )

        self.assertEqual(
            llm_service.conversation_log,
            original_history,
        )
        provider.generate.assert_called_once()

    def test_conversation_failure_preserves_provider_metadata(self):
        """A failed call retains telemetry while rolling history back."""
        original_history = list(llm_service.conversation_log)
        metrics = UsageMetrics(total_duration_seconds=1.25)
        provider = SimpleNamespace(
            generate_with_metadata=mock.Mock(return_value=ProviderCallResult(
                response=None,
                error="provider failed",
                model="resolved-model",
                metrics=metrics,
            )),
        )

        with mock.patch.object(
            llm_service.registry,
            "get_provider_module",
            return_value=provider,
        ), mock.patch.object(
            llm_service.registry,
            "normalize_provider_name",
            return_value="openrouter",
        ), mock.patch.object(
            llm_service.registry,
            "get_provider_type",
            return_value="cloud",
        ):
            result = llm_service.call_provider(
                "openrouter",
                "hello",
            )

        self.assertIsInstance(result, ProviderCallResult)
        self.assertIsNone(result.response)
        self.assertEqual(result.error, "provider failed")
        self.assertEqual(result.model, "resolved-model")
        self.assertEqual(result.metrics, metrics)
        self.assertEqual(llm_service.conversation_log, original_history)

    def test_conversation_cancellation_preserves_provider_metadata(self):
        """Cancellation retains telemetry while rolling history back."""
        original_history = list(llm_service.conversation_log)
        cancel_event = Event()
        metrics = UsageMetrics(total_duration_seconds=0.5)
        def cancel_during_generation(*_args, **_kwargs):
            cancel_event.set()
            return ProviderCallResult(
                response=None,
                error="Request cancelled.",
                model="resolved-model",
                metrics=metrics,
            )

        provider = SimpleNamespace(
            generate_with_metadata=mock.Mock(
                side_effect=cancel_during_generation
            ),
        )

        with mock.patch.object(
            llm_service.registry,
            "get_provider_module",
            return_value=provider,
        ), mock.patch.object(
            llm_service.registry,
            "normalize_provider_name",
            return_value="openrouter",
        ), mock.patch.object(
            llm_service.registry,
            "get_provider_type",
            return_value="cloud",
        ):
            result = llm_service.call_provider(
                "openrouter",
                "hello",
                cancel_event=cancel_event,
            )

        self.assertIsInstance(result, ProviderCallResult)
        self.assertIsNone(result.response)
        self.assertEqual(result.error, "Request cancelled.")
        self.assertEqual(result.model, "resolved-model")
        self.assertEqual(result.metrics, metrics)
        self.assertEqual(llm_service.conversation_log, original_history)

    def test_conversation_call_preserves_provider_metadata(self):
        """Conversation handling does not discard adapter telemetry."""
        metrics = UsageMetrics(
            total_duration_seconds=1.25,
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        )
        provider_result = ProviderCallResult(
            response="hello",
            error=None,
            model="resolved-model",
            metrics=metrics,
        )
        provider = SimpleNamespace(
            generate=mock.Mock(return_value=("legacy response", None)),
            generate_with_metadata=mock.Mock(return_value=provider_result),
        )

        with mock.patch.object(
            llm_service.registry,
            "get_provider_module",
            return_value=provider,
        ), mock.patch.object(
            llm_service.registry,
            "normalize_provider_name",
            return_value="openrouter",
        ), mock.patch.object(
            llm_service.registry,
            "get_provider_type",
            return_value="cloud",
        ):
            result = llm_service.call_provider(
                "openrouter",
                "hello",
            )

        self.assertIsInstance(result, ProviderCallResult)
        self.assertEqual(result.response, "hello")
        self.assertIsNone(result.error)
        self.assertEqual(result.model, "resolved-model")
        self.assertEqual(result.metrics, metrics)
        provider.generate_with_metadata.assert_called_once()
        provider.generate.assert_not_called()

    def test_unconfigured_provider_is_skipped_cleanly(self):
        with mock.patch.dict(
            "os.environ",
            {
                "CODA_CLOUD_PROVIDERS":
                    "openrouter,gemini,openai",
                "OPENROUTER_API_KEY": "",
                "GEMINI_API_KEY": "gemini-key",
                "OPENAI_API_KEY": "openai-key",
            },
            clear=True,
        ):
            providers = (
                core.registry.resolve_provider_list(
                    "openrouter,gemini,openai",
                    provider_type="cloud",
                )
            )

        self.assertEqual(
            providers,
            [
                "gemini",
                "openai",
            ],
        )

    def test_provider_resolution_is_independent_of_developer_env(self):
        with mock.patch.dict(
            "os.environ",
            {
                "CODA_CLOUD_PROVIDERS":
                    "grok,openrouter",
                "XAI_API_KEY": "grok-key",
                "OPENROUTER_API_KEY": "router-key",
                "CODA_OPENROUTER_MODEL": "openrouter/free",
            },
            clear=True,
        ):
            cloud_providers = (
                core.registry.resolve_provider_list(
                    "grok,openrouter",
                    provider_type="cloud",
                )
            )

        self.assertEqual(
            cloud_providers,
            [
                "grok",
                "openrouter",
            ],
        )


if __name__ == "__main__":
    unittest.main()
