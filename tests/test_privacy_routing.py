import unittest
from unittest import mock

from ai.providers import registry
from ai.privacy.detector import analyze_privacy
from ai.router import core
import utils.llm_service as llm_service


class PrivacyRoutingTests(unittest.TestCase):
    def tearDown(self):
        llm_service._reset_conversation()

    def test_cloud_messages_redact_sensitive_history(self):
        local_provider = registry.get_provider_module("ollama")
        cloud_provider = registry.get_provider_module("openai")
        captured = []

        with mock.patch.object(local_provider, "generate", return_value=("local ok", None)):
            llm_service.call_provider("ollama", "my password is swordfish", risk=1.0)

        with mock.patch.object(
            cloud_provider,
            "generate",
            side_effect=lambda messages: (captured.extend(messages) or ("cloud ok", None)),
        ):
            llm_service.call_provider("openai", "say ok", risk=0.0)

        rendered_history = str(captured)
        self.assertNotIn("swordfish", rendered_history)
        self.assertIn("[Sensitive user request redacted for cloud provider.]", rendered_history)
        self.assertIn("[Sensitive assistant response redacted for cloud provider.]", rendered_history)
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

        def fake_call_provider(provider, prompt, risk=0.0, privacy_result=None):
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

        def fake_call_provider(provider, prompt, risk=0.0, privacy_result=None):
            # Make sure the router keeps the richer detector output, not just the score.
            captured.append((provider, risk, privacy_result["categories"]))
            return "ok", None

        with mock.patch.object(core.logger, "should_skip_provider", return_value=False), \
             mock.patch.object(core.logger, "log_attempt"), \
             mock.patch.object(core.llm_service, "call_provider", side_effect=fake_call_provider):
            response, error = core.route_request("email me at test@example.com")

        self.assertEqual((response, error), ("ok", None))
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
                 side_effect=lambda messages: (captured.extend(messages) or ("ok", None)),
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


if __name__ == "__main__":
    unittest.main()
