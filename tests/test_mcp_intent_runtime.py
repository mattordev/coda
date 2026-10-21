"""Tool actions travel through the same entry point as manual and voice input."""

import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from ai.intents import Intent, IntentRegistry, IntentRouter
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

    def configure(self, *, allowed=True, confirm=False, decider=None, commands=None):
        manager = LocalMCPManager([LocalServerDefinition(
            "test", "unused", trusted=True,
            allowed_tools=frozenset({"echo"}) if allowed else frozenset(),
            confirm_tools=frozenset({"echo"}) if confirm else frozenset(),
        )], transport=self.transport, permission_decider=decider)
        runtime.configure_intent_router(commands or {}, mcp_manager=manager)
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
        self.generate.assert_not_called()

    def test_server_failure_keeps_diagnostics_separate_from_speech_and_context(self):
        self.configure()
        self.outcome = ToolResult(False, {
            "isError": True, "content": [{"type": "text", "text": "PermissionError: /private/location"}],
        }, ToolError("mcp_tool_error", "The MCP tool reported a failure."))
        with patch.object(runtime, "record_tool_failure") as log, patch("builtins.print") as output:
            result = runtime.on_command('mcp.test.echo {"message":"typed"}', {})
        self.assertIn("permission", result.response_text)
        self.assertNotIn("/private/location", result.response_text)
        self.assertIn("/private/location", result.display_text)
        self.assertIs(result.tool_result, self.outcome)
        self.assertIn("/private/location", history.conversation_log[-1]["content"])
        log.assert_called_once_with("mcp.test.echo", result.display_text)
        output.assert_called_once_with(f"CODA: {result.display_text}")
        self.fallback.assert_not_called()

    def test_large_listing_keeps_visual_result_and_question_specific_speech(self):
        self.configure()
        text = "\n".join(f"[FILE] file{i}.py" for i in range(100))
        self.outcome = ToolResult(True, {"content": [{"type": "text", "text": text}]})
        result = runtime.on_command('mcp.test.echo {"message":"typed"}', {})
        self.assertIn("100 files", result.response_text)
        self.assertIn("file99.py", result.display_text)
        self.assertIs(result.tool_result, self.outcome)

    def test_original_question_selects_directory_speech_without_changing_display(self):
        self.configure()
        self.outcome = ToolResult(True, {
            "content": [{"type": "text", "text": "[FILE] a-old.py\n[FILE] b-new.py\n[DIR] project"}],
            "structuredContent": {"kind": "directory_listing", "entries": [
                {"name": "a-old.py", "type": "file", "modified_at": 1},
                {"name": "b-new.py", "type": "file", "modified_at": 2},
                {"name": "project", "type": "directory", "modified_at": 99},
            ]},
        })
        overview = runtime.on_command("What's in this folder?", {})
        recent = runtime.on_command("What have I edited recently?", {})
        names = runtime.on_command("Read all filenames", {})
        self.assertEqual(overview.response_text, "The listing contains 1 directory and 2 files.")
        self.assertLess(recent.response_text.index("b-new.py"), recent.response_text.index("a-old.py"))
        self.assertNotIn('"project"', recent.response_text)
        self.assertLess(names.response_text.index("a-old.py"), names.response_text.index("b-new.py"))
        self.assertEqual(overview.display_text, recent.display_text)
        self.assertEqual(recent.display_text, names.display_text)
        self.assertIs(names.tool_result, self.outcome)
        self.fallback.assert_not_called()

    def test_bundled_filesystem_round_trip_through_manual_entrypoint(self):
        from ai.mcp.bundled import bundled_launch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            allowed.mkdir()
            (allowed / "main.py").touch()
            config = root / "permissions.json"
            config.write_text(json.dumps({"read": {
                "allowed_roots": [str(allowed)], "blocked_paths": [],
            }}), encoding="utf-8")
            command, args = bundled_launch("filesystem")
            manager = LocalMCPManager([LocalServerDefinition(
                "filesystem", command, (*args, "--config", str(config)), trusted=True,
                allowed_tools=frozenset({"list_directory"}),
            )])
            runtime.configure_intent_router({}, mcp_manager=manager)
            self.addCleanup(runtime.shutdown_tools)
            result = runtime.on_command('mcp.filesystem.list_directory ' + json.dumps({"path": str(allowed)}), {})
            self.assertTrue(result.tool_result.success, result.tool_result.error)
            self.assertIn("1 file", result.response_text)
            self.assertEqual(result.display_text, "[FILE] main.py")
            self.assertEqual(history.get_recent_tool_result().data["structuredContent"]["entries"],
                             [{"name": "main.py", "type": "file", "modified_at": (allowed / "main.py").stat().st_mtime}])
            self.generate.assert_not_called()
            self.fallback.assert_not_called()

    def test_first_natural_language_request_classifies_once_with_native_and_mcp_catalog(self):
        native = Mock(INTENT=Intent("say", "Speak a message."), run=Mock(return_value=True))
        commands = {"say": native}
        self.configure(commands=commands)

        def classify(messages):
            catalog = json.loads(messages[1]["content"])["available_intents"]
            names = {item["name"] for item in catalog}
            # Reproduce the real model's native choice when echo isn't available.
            selected = "mcp.test.echo" if "mcp.test.echo" in names else "say"
            return json.dumps({"intent": selected, "confidence": 0.95, "arguments": {"message":"hello"}}), None

        self.generate.side_effect = classify
        result = runtime.on_command("use the test echo tool to repeat hello", commands)
        self.assertEqual(result.command_matches, ("mcp.test.echo",))
        self.generate.assert_called_once()
        native.run.assert_not_called()
        names = {item["name"] for item in json.loads(self.generate.call_args.args[0][1]["content"])["available_intents"]}
        self.assertEqual(names, {"say", "mcp.test.echo"})

    def test_deterministic_native_request_does_not_discover_or_classify(self):
        native = Mock(INTENT=Intent("say", "Speak a message."), run=Mock(return_value=True))
        commands = {"say": native}
        self.configure(commands=commands)
        result = runtime.on_command("say hello", commands)
        self.assertTrue(result.handled)
        native.run.assert_called_once()
        self.transport.request.assert_not_called()
        self.generate.assert_not_called()

    def test_failed_discovery_still_allows_native_natural_language_classification(self):
        from ai.tools.models import ToolError
        native = Mock(INTENT=Intent("say", "Speak a message."), run=Mock(return_value=True))
        commands = {"say": native}
        self.configure(commands=commands)
        self.transport.request.side_effect = None
        self.transport.request.return_value = ToolResult(False, error=ToolError("connection_error", "Unavailable."))
        self.generate.return_value = ('{"intent":"say","confidence":0.95}', None)
        result = runtime.on_command("please speak hello", commands)
        self.assertTrue(result.handled)
        native.run.assert_called_once()
        self.generate.assert_called_once()

    def test_explicit_unavailable_tool_never_calls_classifier_or_fallback(self):
        self.configure()
        for message in ("mcp.test.missing {}", "mcp.unknown.echo {}", "mcp.test"):
            with self.subTest(message=message):
                result = runtime.on_command(message, {})
                self.assertIn("unavailable", result.response_text)
        self.generate.assert_not_called()
        self.fallback.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_explicit_first_use_discovers_only_the_named_server(self):
        definitions = [LocalServerDefinition(
            name, "unused", trusted=True, allowed_tools=frozenset({"echo"}),
        ) for name in ("test", "unrelated")]
        manager = LocalMCPManager(definitions, transport=self.transport)
        runtime.configure_intent_router({}, mcp_manager=manager)
        self.addCleanup(runtime.shutdown_tools)
        result = runtime.on_command('mcp.test.echo {"message":"typed"}', {})
        self.assertTrue(result.tool_result.success)
        self.assertEqual([call.args[0].name for call in self.transport.request.call_args_list], ["test", "test"])
        self.generate.assert_not_called()

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
