import unittest
from unittest import mock

from ai.providers import registry
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

        def fake_call_provider(provider, prompt, risk=0.0):
            captured.append((provider, prompt, risk))
            if provider == "ollama":
                return None, "local failed"
            return "ok", None

        with mock.patch.object(core, "detect_privacy", return_value=0.6), \
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
                ("ollama", "email my manager", 0.6),
                ("openai", "email my manager", 0.6),
            ],
        )


if __name__ == "__main__":
    unittest.main()
