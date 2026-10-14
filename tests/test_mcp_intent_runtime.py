"""Tool actions travel through the same entry point as manual and voice input."""

import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from ai.intents import IntentRegistry, IntentRouter
from ai.intents.local_classifier import LocalClassifierStrategy
from ai.intents.router import ExactMatchStrategy, CommandPrefixStrategy
from ai.mcp.config import LocalServerDefinition
from ai.mcp.runtime import LocalMCPManager
from ai.tools.models import ToolError, ToolResult
import utils.on_command as runtime
import utils.llm_service as history


class MCPIntentRuntimeTests(unittest.TestCase):
    def setUp(self):
        for name in ("_mcp_catalog", "_pending_confirmation", "_intent_router", "_intent_registry"):
            guard = patch.object(runtime, name, None)
            guard.start()
            self.addCleanup(guard.stop)
        fallback = patch.object(runtime, "route_request", return_value=("conversation", None))
        self.fallback = fallback.start()
        self.addCleanup(fallback.stop)
        log = patch.object(history, "conversation_log", [])
        log.start()
        self.addCleanup(log.stop)
        self.generate = Mock(return_value=(json.dumps({
            "intent": "mcp.test.echo", "confidence": 0.95, "arguments": {"message": "hello"},
        }), None))
        router = patch.object(runtime, "create_router", side_effect=lambda registry: IntentRouter(
            registry, strategies=(ExactMatchStrategy(), CommandPrefixStrategy(), LocalClassifierStrategy(self.generate)),
        ))
        router.start()
        self.addCleanup(router.stop)
        self.transport = Mock()
        self.calls = []
        self.outcome = ToolResult(success=True, data={"structuredContent": {"message": "hello"}})

        def execute(_definition, operation, **options):
            if operation == "discover":
                return ToolResult(success=True, data={"tools": [{
                    "name": "echo", "description": "Echo a message.", "inputSchema": {
                        "type": "object", "properties": {"message": {"type": "string"}},
                        "required": ["message"], "additionalProperties": False,
                    },
                }]})
            self.calls.append(options)
            return self.outcome

        self.transport.request.side_effect = execute

    def configure(self, *, allowed=True, confirm=False, decider=None):
        manager = LocalMCPManager([LocalServerDefinition(
            "test", "unused", trusted=True,
            allowed_tools=frozenset({"echo"}) if allowed else frozenset(),
            confirm_tools=frozenset({"echo"}) if confirm else frozenset(),
        )], transport=self.transport, permission_decider=decider)
        runtime.configure_intent_router({}, mcp_manager=manager)
        self.addCleanup(runtime.shutdown_tools)
        return manager

    def test_natural_language_selection_extracts_validated_arguments_and_records_context(self):
        self.configure()
        result = runtime.on_command("please echo hello", {})
        self.assertTrue(result.handled)
        self.assertTrue(result.tool_result.success)
        self.assertEqual(self.calls[0]["arguments"], {"message": "hello"})
        self.fallback.assert_not_called()
        self.assertEqual(len(history.conversation_log), 2)
        catalog = json.loads(self.generate.call_args.args[0][1]["content"])["available_intents"]
        self.assertIn("input_schema", catalog[0])

    def test_explicit_json_works_without_argument_model(self):
        self.configure()
        self.generate.return_value = ('{"intent":null,"confidence":0}', None)
        result = runtime.on_command('mcp.test.echo {"message":"typed"}', {})
        self.assertTrue(result.tool_result.success)
        self.assertEqual(self.calls[0]["arguments"], {"message": "typed"})

    def test_invalid_arguments_never_invoke_or_fall_back(self):
        self.configure()
        self.generate.return_value = (json.dumps({"intent":"mcp.test.echo","confidence":1,"arguments":{"message":12}}), None)
        result = runtime.on_command("echo twelve", {})
        self.assertIn("invalid", result.response_text)
        self.assertEqual(self.calls, [])
        self.fallback.assert_not_called()

    def test_denial_is_visible_and_does_not_invoke_or_fall_back(self):
        self.configure(allowed=False)
        result = runtime.on_command("echo hello", {})
        self.assertEqual(result.tool_result.error.code, "permission_denied")
        self.assertIn("not permitted", result.response_text)
        self.assertEqual(self.calls, [])
        self.fallback.assert_not_called()

    def test_confirmation_binds_exact_validated_request_and_is_consumed_once(self):
        self.configure(confirm=True)
        first = runtime.on_command("echo hello", {})
        self.assertIn("Confirm", first.response_text)
        self.assertEqual(self.calls, [])
        confirmed = runtime.on_command("confirm", {})
        self.assertTrue(confirmed.tool_result.success)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["arguments"], {"message":"hello"})
        self.assertIsNone(runtime._pending_confirmation)

    def test_confirmation_expiry_and_cancellation_do_not_execute(self):
        self.configure(confirm=True)
        runtime.on_command("echo hello", {})
        with patch.object(runtime.time, "monotonic", return_value=float("inf")):
            self.assertIn("expired", runtime.on_command("yes", {}).response_text)
        runtime.on_command("echo hello", {})
        self.assertIn("cancelled", runtime.on_command("cancel", {}).response_text)
        self.assertEqual(self.calls, [])

    def test_confirmation_rechecks_dynamic_denial(self):
        decision = Mock(return_value="confirm")
        self.configure(decider=decision)
        runtime.on_command("echo hello", {})
        decision.return_value = "deny"
        result = runtime.on_command("confirm", {})
        self.assertEqual(result.tool_result.error.code, "permission_denied")
        self.assertEqual(self.calls, [])

    def test_runtime_stop_phrase_clears_pending_approval(self):
        import coda_runtime
        self.configure(confirm=True)
        runtime.on_command("echo hello", {})
        self.assertIsNotNone(runtime._pending_confirmation)
        with patch.object(coda_runtime.active_request_state, "cancel_active", return_value=None), \
             patch.object(coda_runtime.runtime_queues.speech, "cancel_current", return_value=None):
            coda_runtime.cancel_active_request()
        self.assertIsNone(runtime._pending_confirmation)
        self.assertEqual(self.calls, [])

    def test_debug_output_sanitizes_argument_secrets(self):
        self.configure()
        secret = "sk-" + "a" * 48
        with patch("builtins.print") as output:
            runtime.on_command('mcp.test.echo ' + json.dumps({"message":secret}), {}, debug=True)
        self.assertNotIn(secret, repr(output.call_args_list))

    def test_failure_never_retries_or_uses_unrelated_fallback(self):
        self.configure()
        self.outcome = ToolResult(False, error=ToolError("timeout", "The MCP operation timed out."))
        result = runtime.on_command("echo hello", {})
        self.assertEqual(result.tool_result.error.code, "timeout")
        self.assertEqual(len(self.calls), 1)
        self.fallback.assert_not_called()

    def test_ambiguous_match_requests_clarification_without_execution(self):
        self.configure()
        self.generate.return_value = ('{"intent":"mcp.test.echo","confidence":0.5}', None)
        result = runtime.on_command("maybe echo something", {})
        self.assertIn("clarify", result.response_text)
        self.assertEqual(self.calls, [])
        self.fallback.assert_not_called()

    def test_genuinely_unmatched_request_keeps_conversation_fallback(self):
        self.configure()
        self.generate.return_value = ('{"intent":null,"confidence":0}', None)
        with patch.object(runtime, "_llm_fallback_enabled", return_value=True):
            result = runtime.on_command("tell me a story", {})
        self.assertTrue(result.used_llm)
        self.fallback.assert_called_once()

    def test_cancelled_request_never_connects(self):
        self.configure()
        event = Event()
        event.set()
        runtime.on_command("echo hello", {}, cancel_event=event)
        self.transport.request.assert_not_called()

    def test_full_result_is_separate_and_cloud_context_is_sanitized_before_bounding(self):
        self.configure()
        secret = "sk-" + "a" * 48
        self.outcome = ToolResult(True, {"padding":"x" * 3000, "secret":secret})
        with patch("ai.tools.context.policy.get_cloud_action", return_value="sanitize"):
            result = runtime.on_command("echo hello", {})
        self.assertEqual(result.tool_result.data["secret"], secret)
        local, cloud = history.conversation_log[-1]["content"], history.conversation_log[-1]["cloud_content"]
        self.assertLessEqual(len(local), 2000)
        self.assertLessEqual(len(cloud), 2000)
        self.assertNotIn(secret, cloud)

    def test_real_stdio_natural_language_request_runs_through_runtime_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = LocalMCPManager([LocalServerDefinition(
                "test", sys.executable,
                args=(str(Path(__file__).with_name("mcp_stdio_server.py")), "normal", str(Path(directory) / "pid")),
                trusted=True, allowed_tools=frozenset({"echo"}), session_mode="persistent",
                startup_timeout_seconds=15,
            )])
            runtime.configure_intent_router({}, mcp_manager=manager)
            try:
                result = runtime.run("please echo hello", {}, cancel_event=Event())
                self.assertEqual(result.tool_result.data["structuredContent"]["message"], "hello")
                self.assertEqual(result.response_text, f"Message: hello; Pid: {result.tool_result.data['structuredContent']['pid']}")
                self.fallback.assert_not_called()
            finally:
                self.assertTrue(runtime.shutdown_tools()["test"].success)
