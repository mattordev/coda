import unittest
from threading import Event
from unittest import mock

from ai.providers import registry
from ai.privacy.detector import analyze_privacy
from ai.privacy import policy
from ai.llm_router import core
import utils.llm_service as llm_service


class PrivacyRoutingTests(unittest.TestCase):
    def tearDown(self):
        llm_service._reset_conversation()

    def test_cancelled_request_does_not_start_provider(self):
        cancel_event = Event()
        cancel_event.set()

        with mock.patch.object(
            core,
            "_call_provider_with_health",
        ) as call_provider:
            response, error = core._try_providers(
                ["openai", "ollama"],
                "cancelled request",
                0.0,
                cancel_event=cancel_event,
            )

        self.assertIsNone(response)
        self.assertEqual(error, "Request cancelled.")
        call_provider.assert_not_called()

    def test_cancellation_after_provider_prevents_fallback(self):
        cancel_event = Event()
        attempted_providers = []

        def call_provider(provider, *_args, **_kwargs):
            attempted_providers.append(provider)
            cancel_event.set()
            return None, "provider failed", False

        with mock.patch.object(
            core,
            "_call_provider_with_health",
            side_effect=call_provider,
        ):
            response, error = core._try_providers(
                ["openai", "ollama"],
                "cancel during provider",
                0.0,
                cancel_event=cancel_event,
            )

        self.assertIsNone(response)
        self.assertEqual(error, "Request cancelled.")
        self.assertEqual(attempted_providers, ["openai"])

    def test_route_preserves_cancellation_for_every_privacy_branch(self):
        cases = (
            ("low", policy.ACTION_RAW),
            ("medium", policy.ACTION_SANITIZE),
            ("high", policy.ACTION_BLOCK),
        )

        for level, cloud_action in cases:
            with self.subTest(level=level), mock.patch.object(
                core,
                "analyze_privacy",
                return_value={
                    "risk": 0.0,
                    "level": level,
                    "categories": [],
                    "matches": [],
                },
            ), mock.patch.object(
                core,
                "get_provider_order",
                return_value=["ollama"],
            ), mock.patch.object(
                core.policy,
                "get_cloud_action",
                return_value=cloud_action,
            ), mock.patch.object(
                core,
                "_try_providers",
                return_value=(None, "Request cancelled."),
            ), mock.patch.object(core, "_debug_print") as debug_print:
                response, error = core.route_request("cancel me")

            self.assertIsNone(response)
            self.assertEqual(error, "Request cancelled.")
            self.assertNotIn(
                mock.call("[ROUTER] All providers failed -> giving up"),
                debug_print.call_args_list,
            )

    def test_router_passes_cancellation_event_to_provider_service(self):
        cancel_event = Event()

        with mock.patch.object(
            core.logger,
            "should_skip_provider",
            return_value=False,
        ), mock.patch.object(core.logger, "log_attempt"), mock.patch.object(
            core.llm_service,
            "call_provider",
            return_value=("ok", None),
        ) as call_provider:
            response, error, skipped = core._call_provider_with_health(
                "ollama",
                "hello",
                0.0,
                cancel_event=cancel_event,
            )

        self.assertEqual((response, error, skipped), ("ok", None, False))
        call_provider.assert_called_once_with(
            "ollama",
            "hello",
            risk=0.0,
            privacy_result=None,
            cancel_event=cancel_event,
        )

    def test_cancelled_provider_request_does_not_change_conversation_history(self):
        cancel_event = Event()
        cancel_event.set()
        provider = registry.get_provider_module("ollama")
        original_history = list(llm_service.conversation_log)

        with mock.patch.object(provider, "generate") as generate:
            response, error = llm_service.call_provider(
                "ollama",
                "cancelled before provider",
                cancel_event=cancel_event,
            )

        self.assertIsNone(response)
        self.assertEqual(error, "Request cancelled.")
        self.assertEqual(llm_service.conversation_log, original_history)
        generate.assert_not_called()

    def test_cancellation_during_provider_discards_request_and_response(self):
        cancel_event = Event()
        provider = registry.get_provider_module("ollama")
        original_history = list(llm_service.conversation_log)

        def cancel_during_generation(_messages, **_kwargs):
            cancel_event.set()
            return "late response", None

        with mock.patch.object(
            provider,
            "generate",
            side_effect=cancel_during_generation,
        ):
            response, error = llm_service.call_provider(
                "ollama",
                "cancelled during provider",
                cancel_event=cancel_event,
            )

        self.assertIsNone(response)
        self.assertEqual(error, "Request cancelled.")
        self.assertEqual(llm_service.conversation_log, original_history)

    def test_cancellation_during_response_analysis_restores_history(self):
        cancel_event = Event()
        provider = registry.get_provider_module("ollama")
        original_history = list(llm_service.conversation_log)
        privacy_result = analyze_privacy("late response")

        def cancel_during_analysis(_response):
            cancel_event.set()
            return privacy_result

        with mock.patch.object(
            provider,
            "generate",
            return_value=("late response", None),
        ), mock.patch.object(
            llm_service,
            "analyze_privacy",
            side_effect=cancel_during_analysis,
        ):
            response, error = llm_service.call_provider(
                "ollama",
                "cancel during analysis",
                cancel_event=cancel_event,
            )

        self.assertIsNone(response)
        self.assertEqual(error, "Request cancelled.")
        self.assertEqual(llm_service.conversation_log, original_history)

    def test_cancellation_after_assistant_append_restores_history(self):
        cancel_event = Event()
        provider = registry.get_provider_module("ollama")
        original_history = list(llm_service.conversation_log)

        def cancel_during_trim():
            cancel_event.set()

        with mock.patch.object(
            provider,
            "generate",
            return_value=("late response", None),
        ), mock.patch.object(
            llm_service,
            "_trim_conversation",
            side_effect=cancel_during_trim,
        ):
            response, error = llm_service.call_provider(
                "ollama",
                "cancel after append",
                cancel_event=cancel_event,
            )

        self.assertIsNone(response)
        self.assertEqual(error, "Request cancelled.")
        self.assertEqual(llm_service.conversation_log, original_history)

    def test_cloud_messages_redact_sensitive_history(self):
        local_provider = registry.get_provider_module("ollama")
        cloud_provider = registry.get_provider_module("openai")
        captured = []

        with mock.patch.object(local_provider, "generate", return_value=("local ok", None)):
            llm_service.call_provider("ollama", "my password is swordfish", risk=1.0)

        with mock.patch.object(
            cloud_provider,
            "generate",
            side_effect=lambda messages, **_kwargs: (
                captured.extend(messages) or ("cloud ok", None)
            ),
        ):
            llm_service.call_provider("openai", "say ok", risk=0.0)

        rendered_history = str(captured)
        self.assertNotIn("swordfish", rendered_history)
        self.assertIn("[Sensitive user request withheld for cloud provider.", rendered_history)
        self.assertIn("[Sensitive assistant response withheld for cloud provider.", rendered_history)
        self.assertEqual(captured[-1], {"role": "user", "content": "say ok"})

    def test_local_messages_keep_sensitive_history(self):
        llm_service.conversation_log[:] = [
            llm_service._new_system_message(),
            llm_service._new_message("user", "my password is swordfish", risk=1.0),
        ]

        messages = llm_service._build_messages_for_provider("ollama")

        self.assertIn(
            {"role": "user", "content": "my password is swordfish"},
            messages,
        )

    def test_router_passes_risk_to_fallback_provider(self):
        captured = []

        def fake_call_provider(
            provider,
            prompt,
            risk=0.0,
            privacy_result=None,
            cancel_event=None,
        ):
            captured.append((provider, prompt, risk, privacy_result["categories"]))
            if provider == "ollama":
                return None, "local failed"
            return "ok", None

        privacy_result = analyze_privacy("email my manager")

        with mock.patch.object(core, "analyze_privacy", return_value=privacy_result), \
             mock.patch.object(core, "get_provider_order", return_value=["ollama", "openai"]), \
             mock.patch.object(core.logger, "should_skip_provider", return_value=False), \
             mock.patch.object(core.logger, "log_attempt"), \
             mock.patch.object(core.logger, "log_failure"), \
             mock.patch.object(core.llm_service, "call_provider", side_effect=fake_call_provider):
            response, error = core.route_request("email my manager")

        self.assertEqual((response, error), ("ok", None))
        self.assertEqual(
            captured,
            [
                ("ollama", "email my manager", 0.6000000000000001, ["personal_context"]),
                ("openai", "email my manager", 0.6000000000000001, ["personal_context"]),
            ],
        )

    def test_router_passes_privacy_result_to_provider_calls(self):
        captured = []

        def fake_call_provider(
            provider,
            prompt,
            risk=0.0,
            privacy_result=None,
            cancel_event=None,
        ):
            # Make sure the router keeps the richer detector output,
            # not just the score.
            captured.append(
                (
                    provider,
                    risk,
                    privacy_result["categories"],
                )
            )
            return "ok", None

        with mock.patch.object(
            core,
            "get_provider_order",
            return_value=["ollama"],
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
                "email me at test@example.com"
            )

        self.assertEqual(
            (response, error),
            ("ok", None),
        )
        self.assertEqual(
            captured,
            [
                (
                    "ollama",
                    0.6000000000000001,
                    ["email", "personal_context"],
                ),
            ],
        )

    def test_cloud_fallback_receives_sanitised_medium_risk_content(self):
        local_provider = registry.get_provider_module("ollama")
        cloud_provider = registry.get_provider_module("openai")
        captured = []

        with mock.patch.object(local_provider, "generate", return_value=(None, "local failed")), \
             mock.patch.object(
                 cloud_provider,
                 "generate",
                 side_effect=lambda messages, **_kwargs: (
                     captured.extend(messages) or ("ok", None)
                 ),
             ), \
             mock.patch.object(
                 core,
                 "get_provider_order",
                 return_value=["ollama", "openai"],
             ), \
             mock.patch.object(core.logger, "should_skip_provider", return_value=False), \
             mock.patch.object(core.logger, "log_attempt"), \
             mock.patch.object(core.logger, "log_failure"):
            response, error = core.route_request("email me at test@example.com")

        self.assertEqual((response, error), ("ok", None))
        self.assertEqual(
            captured[-1],
            {"role": "user", "content": "email me at [email redacted]"},
        )

    def test_default_policy_cloud_actions(self):
        with mock.patch.dict(
            "os.environ",
            {
                "CODA_PRIVACY_MODE": "balanced",
                "CODA_CLOUD_PRIVACY_ACTION": "sanitize",
                "CODA_HIGH_RISK_CLOUD_FALLBACK": "block",
            },
        ):
            # Make sure the default policy keeps low risk raw, medium sanitised, high blocked.
            self.assertEqual(policy.get_cloud_action({"level": "low"}), policy.ACTION_RAW)
            self.assertEqual(
                policy.get_cloud_action({"level": "medium"}),
                policy.ACTION_SANITIZE,
            )
            self.assertEqual(
                policy.get_cloud_action({"level": "high"}),
                policy.ACTION_BLOCK,
            )

    def test_strict_mode_blocks_medium_risk_cloud_fallback(self):
        privacy_result = analyze_privacy("email me at test@example.com")

        with mock.patch.dict("os.environ", {"CODA_PRIVACY_MODE": "strict"}), \
             mock.patch.object(
                 core,
                 "_get_configured_provider_groups",
                 return_value=(["openai"], ["ollama"]),
             ):
            # Make sure strict mode keeps sensitive requests local-only.
            self.assertEqual(core.get_provider_order(privacy_result), ["ollama"])

    def test_permissive_mode_allows_high_risk_cloud_fallback(self):
        privacy_result = analyze_privacy("my password is swordfish")

        with mock.patch.dict(
            "os.environ",
            {
                "CODA_PRIVACY_MODE": "permissive",
                "CODA_HIGH_RISK_CLOUD_FALLBACK": "sanitize",
            },
        ), \
             mock.patch.object(
                 core,
                 "_get_configured_provider_groups",
                 return_value=(["openai"], ["ollama"]),
             ):
            # Make sure permissive mode can still fallback to cloud after local fails.
            self.assertEqual(core.get_provider_order(privacy_result), ["ollama", "openai"])

    def test_summarize_action_renders_cloud_safe_summary(self):
        privacy_result = analyze_privacy("email me at test@example.com")

        with mock.patch.dict("os.environ", {"CODA_CLOUD_PRIVACY_ACTION": "summarize"}):
            message = llm_service._new_message(
                "user",
                "email me at test@example.com",
                risk=privacy_result["risk"],
                privacy_result=privacy_result,
            )

        # Make sure summarize mode hides the original value but keeps useful categories.
        self.assertEqual(
            message["cloud_content"],
            "[Sensitive user request withheld for cloud provider. Categories: email, personal_context.]",
        )

    def test_sensitive_assistant_response_is_sanitised_for_future_cloud_history(self):
        cloud_provider = registry.get_provider_module("openai")
        captured = []

        with mock.patch.object(
            cloud_provider,
            "generate",
            return_value=("contact me at private@example.com", None),
        ):
            llm_service.call_provider("openai", "say contact details", risk=0.0)

        with mock.patch.object(
            cloud_provider,
            "generate",
            side_effect=lambda messages, **_kwargs: (
                captured.extend(messages) or ("ok", None)
            ),
        ):
            llm_service.call_provider("openai", "say ok", risk=0.0)

        rendered_history = str(captured)
        # Make sure provider-generated sensitive content does not leak into the next cloud call.
        self.assertNotIn("private@example.com", rendered_history)
        self.assertIn(
            {"role": "assistant", "content": "contact me at [email redacted]"},
            captured,
        )


if __name__ == "__main__":
    unittest.main()
