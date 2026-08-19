import unittest
from threading import Event
from unittest import mock

from ai.llm_router import core
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

        provider = mock.MagicMock()
        provider.generate.return_value = (
            None,
            "provider failed",
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