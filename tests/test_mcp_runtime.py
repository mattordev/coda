"""Local MCP discovery and execution through CODA-owned interfaces."""

from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Event, Thread
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import psutil

from ai.intents import Intent, IntentDispatcher, IntentRegistry, IntentRequest, IntentRouter
from ai.mcp.config import LocalServerDefinition
from ai.mcp.runtime import LocalMCPManager, ServerState
from ai.tools.models import ToolResult
from ai.mcp import transport as sdk_boundary


SERVER = Path(__file__).with_name("mcp_stdio_server.py")


class MCPSDKBoundaryTests(unittest.TestCase):
    def test_sdk_timeout_is_normalized_even_before_polling_deadline(self):
        definition = LocalServerDefinition("test", "unused", trusted=True)

        class TimeoutClient:
            async def __aenter__(self):
                raise sdk_boundary.MCPError(sdk_boundary.types.REQUEST_TIMEOUT, "private-timeout-token")

            async def __aexit__(self, *_):
                return None

        with patch.object(sdk_boundary, "Client", return_value=TimeoutClient()):
            result = sdk_boundary.SDKStdioTransport().request(definition, "discover")

        self.assertEqual(result.error.code, "timeout")
        self.assertNotIn("private-timeout-token", repr(result))

    def test_transport_itself_denies_unallowlisted_calls_before_launch(self):
        definition = LocalServerDefinition("test", "unused", trusted=True)
        with patch.object(sdk_boundary, "Client") as client:
            result = sdk_boundary.SDKStdioTransport().request(definition, "invoke", tool_name="echo")

        self.assertEqual(result.error.code, "permission_denied")
        client.assert_not_called()


class MCPLifecycleTests(unittest.TestCase):
    def test_retirement_prevents_new_work_until_cleanup_finishes(self):
        active = Event()
        cleanup_started = Event()
        cleanup_allowed = Event()
        results = []
        retire_results = []

        def operation(_definition, _operation, **kwargs):
            active.set()
            if not kwargs["retire_event"].wait(timeout=5):
                raise RuntimeError("Test retirement did not arrive.")
            cleanup_started.set()
            cleanup_allowed.wait(timeout=5)
            return sdk_boundary.failure("cancelled", "Cancelled.")

        transport = Mock(request=Mock(side_effect=operation))
        manager = LocalMCPManager([
            LocalServerDefinition("test", sys.executable, trusted=True)
        ], transport=transport)
        worker = Thread(target=lambda: results.append(manager.discover("test")))
        retire_worker = Thread(target=lambda: retire_results.append(manager.retire("test")))
        self.addCleanup(lambda: (cleanup_allowed.set(), worker.join(timeout=5)))
        worker.start()
        self.assertTrue(active.wait(timeout=5))
        retire_worker.start()
        self.addCleanup(lambda: (cleanup_allowed.set(), retire_worker.join(timeout=5)))
        self.assertTrue(cleanup_started.wait(timeout=5))

        self.assertEqual(manager.discover("test").error.code, "cancelled")
        self.assertEqual(transport.request.call_count, 1)
        cleanup_allowed.set()
        worker.join(timeout=5)
        retire_worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(retire_worker.is_alive())
        self.assertTrue(retire_results[0].success)
        self.assertEqual(manager.state("test"), ServerState.RETIRED)

    def fake_manager(self, *, permission_decider=None):
        definition = LocalServerDefinition(
            "test", sys.executable, trusted=True, allowed_tools=frozenset({"echo"})
        )
        transport = Mock()
        transport.request.return_value = ToolResult(success=True, data={"tools": [{
            "name": "echo", "inputSchema": {
                "type": "object", "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        }]})
        manager = LocalMCPManager([definition], transport=transport, permission_decider=permission_decider)
        found = manager.discover("test")
        self.assertTrue(found.success)
        transport.request.reset_mock()
        return manager, transport, found.data[0]

    def test_discovery_schema_cannot_be_mutated_to_bypass_validation(self):
        manager, transport, intent = self.fake_manager()
        executor = manager.executor(intent.name)
        intent.input_schema["properties"]["message"]["type"] = "integer"
        request = IntentRequest(intent, "echo", 1.0, arguments={"message": 12})

        self.assertEqual(executor.execute(request).error.code, "invalid_arguments")
        transport.request.assert_not_called()

    def test_old_executor_is_rejected_after_tool_disappears(self):
        manager, transport, intent = self.fake_manager()
        executor = manager.executor(intent.name)
        transport.request.return_value = ToolResult(success=True, data={"tools": []})
        self.assertTrue(manager.discover("test").success)
        transport.request.reset_mock()

        request = IntentRequest(intent, "echo", 1.0, arguments={"message": "hello"})
        self.assertEqual(executor.execute(request).error.code, "tool_not_found")
        transport.request.assert_not_called()

    def test_permission_hook_errors_and_unknown_decisions_fail_closed(self):
        for decider in (Mock(side_effect=RuntimeError("private-token")), lambda *_: "unknown"):
            with self.subTest(decider=decider):
                manager, transport, intent = self.fake_manager(permission_decider=decider)
                request = IntentRequest(intent, "echo", 1.0, arguments={"message": "hello"})
                self.assertEqual(manager.executor(intent.name).execute(request).error.code, "permission_denied")
                transport.request.assert_not_called()

    def test_construction_and_configuration_do_not_start_servers(self):
        transport = Mock()
        definition = LocalServerDefinition("test", sys.executable, trusted=True)
        manager = LocalMCPManager([definition], transport=transport)

        self.assertEqual(manager.state("test"), ServerState.CONFIGURED)
        transport.request.assert_not_called()

    def test_untrusted_process_is_never_started(self):
        transport = Mock()
        manager = LocalMCPManager([LocalServerDefinition("test", sys.executable)], transport=transport)

        self.assertEqual(manager.discover("test").error.code, "permission_denied")
        transport.request.assert_not_called()

    def test_invalid_catalog_is_atomic_and_ignores_launch_metadata(self):
        definition = LocalServerDefinition("test", sys.executable, trusted=True)
        transport = Mock()
        transport.request.return_value = ToolResult(success=True, data={"tools": [
            {"name": "echo", "inputSchema": {"type": "object"}, "command": "evil"},
            {"name": "ECHO", "inputSchema": {"type": "object"}},
        ]})
        manager = LocalMCPManager([definition], transport=transport)

        self.assertEqual(manager.discover("test").error.code, "invalid_tool_metadata")
        with self.assertRaises(ValueError):
            manager.executor("mcp.test.echo")
        self.assertEqual(transport.request.call_args.args[0].command, sys.executable)

    def test_unknown_server_and_tool_fail_clearly(self):
        manager = LocalMCPManager([])
        self.assertEqual(manager.discover("missing").error.code, "server_not_found")
        self.assertEqual(manager.retire("missing").error.code, "server_not_found")
        with self.assertRaises(ValueError):
            manager.executor("missing")


class MCPStdioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.pid_path = self.directory / "pid"
        self.started_path = self.directory / "started"
        self.definition = LocalServerDefinition(
            "test", sys.executable,
            args=(str(SERVER), "normal", str(self.pid_path), str(self.started_path)),
            trusted=True,
            allowed_tools=frozenset({"echo", "slow", "domain_failure", "disconnect_call", "malformed_call", "large_result", "environment"}),
            startup_timeout_seconds=15.0, timeout_seconds=5.0,
        )

    def assert_process_stopped(self):
        if self.pid_path.exists():
            self.assertFalse(psutil.pid_exists(int(self.pid_path.read_text(encoding="utf-8"))))

    def discover(self, definition=None, **kwargs):
        manager = LocalMCPManager([definition or self.definition], **kwargs)
        found = manager.discover("test")
        self.assertTrue(found.success, found.error)
        self.assert_process_stopped()
        return manager, {intent.name.rsplit(".", 1)[1]: intent for intent in found.data}

    def test_real_discovery_router_dispatch_invocation_and_cleanup(self):
        manager, intents = self.discover()
        self.assertEqual(manager.state("test"), ServerState.AVAILABLE)
        intent = intents["echo"]
        self.assertEqual(intent.source, "mcp")
        self.assertEqual(intent.parameters[0].name, "message")
        registry = IntentRegistry()
        registry.register(intent)
        dispatcher = IntentDispatcher({})
        dispatcher.register(intent.name, manager.executor(intent.name))
        request = IntentRouter(registry).route(intent.name).to_request(
            intent.name, arguments={"message": "hello"}
        )

        result = dispatcher.execute(request)

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.data["structuredContent"]["message"], "hello")
        self.assertEqual(manager.state("test"), ServerState.RETIRED)
        self.assert_process_stopped()

    def test_real_server_can_be_configured_from_independent_definition_file(self):
        (self.directory / "server.json").write_text(json.dumps({
            "id": "test", "command": sys.executable, "args": list(self.definition.args),
            "trusted": True, "allowed_tools": ["echo"],
        }), encoding="utf-8")
        manager, errors = LocalMCPManager.from_directory(self.directory)
        self.assertEqual(errors, {})

        found = manager.discover("test")
        self.assertTrue(found.success, found.error)
        intent = next(intent for intent in found.data if intent.name == "mcp.test.echo")
        request = IntentRequest(intent, "echo", 1.0, arguments={"message": "hello"})
        self.assertTrue(manager.executor(intent.name).execute(request).success)
        self.assert_process_stopped()

    def test_failure_of_one_server_does_not_disable_another_server(self):
        broken = replace(self.definition, name="broken", command=str(self.directory / "missing-executable"))
        manager = LocalMCPManager([broken, self.definition])

        self.assertEqual(manager.discover("broken").error.code, "connection_error")
        found = manager.discover("test")
        self.assertTrue(found.success, found.error)
        intent = next(intent for intent in found.data if intent.name == "mcp.test.echo")
        request = IntentRequest(intent, "echo", 1.0, arguments={"message": "hello"})
        self.assertTrue(manager.executor(intent.name).execute(request).success)
        self.assert_process_stopped()

    def test_active_server_does_not_block_an_unrelated_server(self):
        other_pid_path = self.directory / "other-pid"
        definition = replace(self.definition, timeout_seconds=20)
        other = replace(definition, name="other", args=(str(SERVER), "normal", str(other_pid_path)))
        manager = LocalMCPManager([definition, other])
        found = manager.discover("test")
        self.assertTrue(found.success)
        slow_intent = next(intent for intent in found.data if intent.name == "mcp.test.slow")
        request = IntentRequest(slow_intent, "slow", 1.0)
        cancel = Event()
        results = []
        thread = Thread(target=lambda: results.append(manager.executor(slow_intent.name).execute(request, cancel_event=cancel)))
        thread.start()
        self.addCleanup(lambda: (cancel.set(), thread.join(timeout=10)))
        deadline = time.monotonic() + 10
        while not self.started_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.started_path.exists())

        other_found = manager.discover("other")
        self.assertTrue(other_found.success, other_found.error)
        self.assertTrue(thread.is_alive())
        self.assertFalse(psutil.pid_exists(int(other_pid_path.read_text(encoding="utf-8"))))
        cancel.set()
        thread.join(timeout=8)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].error.code, "cancelled")
        self.assert_process_stopped()

    def test_only_explicit_environment_references_reach_the_child(self):
        definition = replace(self.definition, env_refs=(("CODA_TEST_SECRET", "CODA_TEST_PARENT_SECRET"),))
        with patch.dict(os.environ, {
            "CODA_TEST_PARENT_SECRET": "private-value",
            "CODA_TEST_OTHER_SECRET": "unreferenced-private-value",
        }):
            manager, intents = self.discover(definition)
            request = IntentRequest(intents["environment"], "environment", 1.0)
            result = manager.executor(request.intent.name).execute(request)

        self.assertTrue(result.success, result.error)
        text = "".join(block.get("text", "") for block in result.data["content"])
        values = json.loads(text)
        self.assertEqual(values["explicit"], "private-value")
        self.assertIsNone(values["unreferenced"])
        self.assert_process_stopped()

    def test_pre_cancelled_executor_through_dispatcher_does_not_launch_process(self):
        manager, intents = self.discover()
        self.pid_path.unlink()
        cancel = Event()
        cancel.set()
        dispatcher = IntentDispatcher({})
        dispatcher.register(intents["echo"].name, manager.executor(intents["echo"].name, cancel_event=cancel))
        request = IntentRequest(intents["echo"], "echo", 1.0, arguments={"message": "hello"})

        self.assertEqual(dispatcher.execute(request).error.code, "cancelled")
        self.assertFalse(self.pid_path.exists())

    def test_output_limit_is_configurable_and_process_is_cleaned_up(self):
        manager, intents = self.discover(replace(self.definition, max_output_bytes=32768))
        request = IntentRequest(intents["large_result"], "large_result", 1.0, arguments={"size": 65536})

        self.assertEqual(manager.executor(request.intent.name).execute(request).error.code, "output_limit")
        self.assert_process_stopped()

    def test_malformed_call_response_does_not_leak_sdk_diagnostics(self):
        manager, intents = self.discover()
        request = IntentRequest(intents["malformed_call"], "malformed_call", 1.0)

        with self.assertNoLogs("mcp", level="DEBUG"):
            result = manager.executor(request.intent.name).execute(request)

        self.assertEqual(result.error.code, "mcp_protocol_error")
        self.assertNotIn("private-malformed-token", repr(result))
        self.assert_process_stopped()

    def test_real_malformed_metadata_is_rejected_after_cleanup(self):
        definition = replace(self.definition, args=(str(SERVER), "invalid_catalog", str(self.pid_path)))

        result = LocalMCPManager([definition]).discover("test")

        self.assertEqual(result.error.code, "invalid_tool_metadata")
        self.assert_process_stopped()

    def test_default_deny_and_context_confirmation_do_not_start_a_process(self):
        definition = replace(self.definition, allowed_tools=frozenset())
        manager, intents = self.discover(definition)
        self.pid_path.unlink()
        request = IntentRequest(intents["echo"], "echo", 1.0, arguments={"message": "hello"})
        self.assertEqual(manager.executor(request.intent.name).execute(request).error.code, "permission_denied")
        self.assertFalse(self.pid_path.exists())

        manager, intents = self.discover(permission_decider=lambda *_: "confirm")
        self.pid_path.unlink()
        request = replace(request, intent=intents["echo"])
        self.assertEqual(manager.executor(request.intent.name).execute(request).error.code, "confirmation_required")
        self.assertFalse(self.pid_path.exists())

    def test_invalid_arguments_do_not_launch_a_server(self):
        manager, intents = self.discover()
        self.pid_path.unlink()
        request = IntentRequest(intents["echo"], "echo", 1.0, arguments={"message": 12})

        result = manager.executor(request.intent.name).execute(request)

        self.assertEqual(result.error.code, "invalid_arguments")
        self.assertFalse(self.pid_path.exists())

    def test_tool_failure_and_disconnect_do_not_disable_native_commands(self):
        manager, intents = self.discover()
        for name, expected in (("domain_failure", "mcp_tool_error"), ("disconnect_call", "mcp_protocol_error")):
            request = IntentRequest(intents[name], name, 1.0)
            result = manager.executor(request.intent.name).execute(request)
            self.assertEqual(result.error.code, expected)
            self.assertNotIn("private-domain-token", result.error.message)
            self.assert_process_stopped()
        native = Mock(run=Mock(return_value=True))
        dispatcher = IntentDispatcher({"native": native})
        self.assertTrue(dispatcher.dispatch(IntentRequest(Intent("native", "Native."), "native", 1.0)))

    def test_timeout_kills_and_reaps_the_owned_process(self):
        definition = replace(self.definition, timeout_seconds=0.2)
        manager, intents = self.discover(definition)
        request = IntentRequest(intents["slow"], "slow", 1.0)
        started = time.monotonic()

        result = manager.executor(request.intent.name).execute(request)

        self.assertEqual(result.error.code, "timeout")
        self.assertLess(time.monotonic() - started, 8.0)
        self.assert_process_stopped()

    def test_cancellation_propagates_and_cleans_up(self):
        manager, intents = self.discover()
        request = IntentRequest(intents["slow"], "slow", 1.0)
        cancel = Event()
        results = []
        thread = Thread(target=lambda: results.append(
            manager.executor(request.intent.name).execute(request, cancel_event=cancel)
        ))
        thread.start()
        self.addCleanup(lambda: (cancel.set(), thread.join(timeout=10)))
        deadline = time.monotonic() + 10
        while not self.started_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.started_path.exists())
        cancel.set()
        thread.join(timeout=8)

        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].error.code, "cancelled")
        self.assert_process_stopped()

    def test_retirement_cancels_active_work_and_permits_later_reconnection(self):
        manager, intents = self.discover()
        request = IntentRequest(intents["slow"], "slow", 1.0)
        results = []
        thread = Thread(target=lambda: results.append(manager.executor(request.intent.name).execute(request)))
        thread.start()
        self.addCleanup(lambda: (manager.retire("test"), thread.join(timeout=10)))
        deadline = time.monotonic() + 10
        while not self.started_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.started_path.exists())

        self.assertTrue(manager.retire("test").success)
        thread.join(timeout=8)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].error.code, "cancelled")
        self.assert_process_stopped()
        self.assertTrue(manager.discover("test").success)

    def test_startup_timeout_and_malformed_stdout_are_bounded_and_private(self):
        for mode in ("hang", "malformed", "disconnect"):
            with self.subTest(mode=mode):
                definition = replace(
                    self.definition,
                    args=(str(SERVER), mode, str(self.pid_path)),
                    startup_timeout_seconds=0.5,
                )
                manager = LocalMCPManager([definition])
                started = time.monotonic()
                result = manager.discover("test")

                self.assertFalse(result.success)
                self.assertLess(time.monotonic() - started, 8.0)
                self.assertNotIn("private-malformed-token", repr(result))
                self.assert_process_stopped()


if __name__ == "__main__":
    unittest.main()
