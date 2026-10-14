"""Real Streamable HTTP authentication, routing, session and failure tests."""

from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from ai.intents import IntentRequest, IntentRouter
from ai.intents import Intent, IntentDispatcher
from unittest.mock import Mock
from ai.intents.router import ExactMatchStrategy, CommandPrefixStrategy
from ai.mcp.config import discover_definitions
from ai.mcp.remote import RemoteServerDefinition
from ai.mcp.runtime import LocalMCPManager, ServerState
import utils.on_command as runtime


class RemoteMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.servers = []
        self.env = patch.dict(os.environ, {"CODA_REMOTE_CREDENTIAL": "Bearer private-test-token"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def server(self, mode="normal"):
        ready = self.directory / f"ready-{len(self.servers)}"
        actions = self.directory / f"actions-{len(self.servers)}"
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("mcp_http_server.py")), str(ready), mode, str(actions)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.servers.append(process)

        def cleanup():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(10)

        self.addCleanup(cleanup)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if ready.exists():
                port = int(ready.read_text())
                with socket.socket() as connection:
                    if connection.connect_ex(("127.0.0.1", port)) == 0:
                        return f"http://127.0.0.1:{port}/mcp", process, actions
            if process.poll() is not None:
                self.fail("Disposable HTTP server exited during startup.")
            time.sleep(0.01)
        self.fail("Disposable HTTP server did not become ready.")

    def definition(self, url, **changes):
        return replace(RemoteServerDefinition(
            "remote", url, header_env_refs=(("Authorization", "CODA_REMOTE_CREDENTIAL"),),
            trusted=True, allowed_tools=frozenset({"echo", "increment", "slow", "disconnect_call", "large_result", "domain_failure"}),
            startup_timeout_seconds=5, timeout_seconds=3,
        ), **changes)

    def manager(self, definition):
        manager = LocalMCPManager([definition])
        self.addCleanup(manager.close)
        return manager

    def invoke(self, manager, name="increment", arguments=None, **options):
        executor = manager.executor(f"mcp.remote.{name}")
        return executor.execute(IntentRequest(executor._intent, name, 1, arguments=arguments or {}), **options)

    def test_configuration_loads_local_and_remote_without_connections(self):
        (self.directory / "local.json").write_text(json.dumps({"id":"local", "command":"unused"}))
        (self.directory / "remote.json").write_text(json.dumps({
            "id":"remote", "transport":"streamable-http", "url":"https://example.invalid/mcp",
            "header_env":{"Authorization":"CODA_REMOTE_CREDENTIAL"}, "session_mode":"persistent",
        }))
        found = discover_definitions(self.directory)
        self.assertEqual(found.errors, {})
        self.assertEqual(len(found.servers), 2)
        self.assertNotIn("private-test-token", repr(found.servers))
        self.assertFalse(found.servers[1].trusted)

    def test_invalid_endpoint_credentials_headers_and_limits_fail_safely(self):
        definition = self.definition("https://example.invalid/mcp")
        for changes in (
            {"url":"http://example.invalid/mcp"}, {"url":"https://user:secret@example.invalid/mcp"},
            {"url":"https://example.invalid/mcp?token=secret"}, {"url":"ftp://example.invalid"},
            {"header_env_refs":(("Authorization", "literal-token-value"),)},
            {"header_env_refs":(("Host", "CODA_REMOTE_CREDENTIAL"),)},
            {"header_env_refs":(("Authorization", "KEY"), ("authorization", "OTHER"))},
            {"max_sse_event_bytes":True}, {"reconnect_backoff_seconds":-1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(definition, **changes)

    def test_real_discovery_invocation_and_persistent_session_reuse(self):
        url, process, _ = self.server()
        manager = self.manager(self.definition(url, session_mode="persistent"))
        self.assertTrue(manager.discover("remote").success)
        first = self.invoke(manager).data["structuredContent"]
        second = self.invoke(manager).data["structuredContent"]
        self.assertEqual(manager.state("remote"), ServerState.CONNECTED)
        self.assertEqual((first["count"], second["count"]), (1,2))
        self.assertEqual(second["initializations"], first["initializations"])
        self.assertTrue(manager.retire("remote").success)
        self.assertIsNone(process.poll())
        fresh = self.invoke(manager).data["structuredContent"]
        self.assertGreater(fresh["initializations"], second["initializations"])

    def test_remote_runtime_uses_common_manual_tool_path_without_model(self):
        url, _, _ = self.server()
        manager = self.manager(self.definition(url))
        with patch.object(runtime, "_mcp_catalog", None), patch.object(runtime, "_pending_confirmation", None), \
             patch.object(runtime, "_intent_router", None), patch.object(runtime, "_intent_registry", None), \
             patch.object(runtime, "create_router", side_effect=lambda registry: IntentRouter(registry, strategies=(
                 ExactMatchStrategy(), CommandPrefixStrategy(),
             ))), patch.object(runtime, "route_request") as fallback:
            runtime.configure_intent_router({}, mcp_manager=manager)
            result = runtime.run('mcp.remote.echo {"message":"remote hello"}', {})
            self.assertEqual(result.tool_result.data["structuredContent"]["message"], "remote hello")
            fallback.assert_not_called()
            runtime.shutdown_tools()

    def test_authentication_failure_and_sdk_logs_do_not_expose_credentials(self):
        url, _, _ = self.server()
        manager = self.manager(self.definition(url))
        with patch.dict(os.environ, {"CODA_REMOTE_CREDENTIAL":"Bearer wrong-private-token"}), \
             self.assertLogs(level=logging.DEBUG) as records:
            logging.getLogger("coda-test").debug("capture marker")
            result = manager.discover("remote")
        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "authentication_error")
        self.assertNotIn("wrong-private-token", repr(result) + repr(records.output))
        self.assertNotIn("private-auth-token", repr(records.output))

    def test_missing_credential_and_default_deny_do_not_connect(self):
        definition = self.definition("https://example.invalid/mcp")
        manager = self.manager(definition)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(manager.discover("remote").error.code, "configuration_error")
        denied = self.manager(replace(definition, trusted=False))
        self.assertEqual(denied.discover("remote").error.code, "permission_denied")

    def test_malformed_timeout_and_server_payload_rejection_are_bounded(self):
        for mode, expected in (("malformed","connection_error"), ("hang","timeout"), ("too_large","output_limit")):
            with self.subTest(mode=mode):
                url, process, _ = self.server(mode)
                manager = self.manager(self.definition(url, startup_timeout_seconds=0.4, timeout_seconds=0.4))
                start = time.monotonic()
                result = manager.discover("remote")
                self.assertFalse(result.success)
                self.assertEqual(result.error.code, expected)
                self.assertLess(time.monotonic() - start, 5)
                self.assertNotIn("private-", repr(result))
                self.assertIsNone(process.poll())

    def test_timeout_cancellation_and_later_recovery(self):
        url, process, actions = self.server()
        manager = self.manager(self.definition(url, session_mode="persistent", timeout_seconds=0.4))
        self.assertTrue(manager.discover("remote").success)
        timed_out = self.invoke(manager, "slow", {"delay":60})
        self.assertEqual(timed_out.error.code, "timeout")
        self.assertTrue(self.invoke(manager).success)
        actions.unlink(missing_ok=True)
        event, results = Event(), []
        worker = Thread(target=lambda: results.append(self.invoke(manager, "slow", {"delay":60}, cancel_event=event)))
        worker.start()
        self.addCleanup(lambda: (event.set(), worker.join(10)))
        deadline = time.monotonic() + 3
        while not actions.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(actions.exists())
        event.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].error.code, "cancelled")
        self.assertTrue(self.invoke(manager).success)
        self.assertTrue(manager.close()["remote"].success)
        self.assertIsNone(process.poll())

    def test_accepted_disconnect_is_never_replayed_and_backoff_is_enforced(self):
        url, process, actions = self.server()
        manager = self.manager(self.definition(url, reconnect_backoff_seconds=10))
        self.assertTrue(manager.discover("remote").success)
        result = self.invoke(manager, "disconnect_call")
        self.assertFalse(result.success)
        process.wait(10)
        self.assertEqual(actions.read_text().splitlines(), ["accepted"])
        self.assertEqual(self.invoke(manager).error.code, "connection_backoff")

    def test_domain_failure_allowlist_and_full_result_limit_use_common_errors(self):
        url, _, _ = self.server()
        manager = self.manager(self.definition(url))
        self.assertTrue(manager.discover("remote").success)
        self.assertEqual(self.invoke(manager, "domain_failure").error.code, "mcp_tool_error")
        self.assertEqual(self.invoke(manager, "large_result", {"size":5 * 1024 * 1024}).error.code, "output_limit")
        denied = self.manager(self.definition(url, allowed_tools=frozenset()))
        self.assertTrue(denied.discover("remote").success)
        self.assertEqual(self.invoke(denied).error.code, "permission_denied")

    def test_streamed_results_and_configurable_sse_event_limit(self):
        url, _, _ = self.server("sse")
        manager = self.manager(self.definition(url, max_sse_event_bytes=16384))
        self.assertTrue(manager.discover("remote").success)
        self.assertTrue(self.invoke(manager, "echo", {"message":"streamed"}).success)
        result = self.invoke(manager, "large_result", {"size":32768})
        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "mcp_protocol_error")

    def test_busy_remote_server_does_not_block_other_servers_or_native_commands(self):
        url, _, actions = self.server()
        definition = self.definition(url, session_mode="persistent", timeout_seconds=5)
        manager = LocalMCPManager([definition, replace(definition, name="other")])
        self.addCleanup(manager.close)
        self.assertTrue(manager.discover("remote").success)
        self.assertTrue(manager.discover("other").success)
        event, results = Event(), []
        worker = Thread(target=lambda: results.append(self.invoke(manager, "slow", {"delay":60}, cancel_event=event)))
        worker.start()
        self.addCleanup(lambda: (event.set(), worker.join(10)))
        deadline = time.monotonic() + 3
        while not actions.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(actions.exists())
        native = Mock(run=Mock(return_value=True))
        self.assertTrue(IntentDispatcher({"native":native}).execute(IntentRequest(Intent("native","Native."), "native", 1)).success)
        executor = manager.executor("mcp.other.increment")
        self.assertTrue(executor.execute(IntentRequest(executor._intent,"increment",1,arguments={})).success)
        event.set()
        worker.join(10)
        self.assertEqual(results[0].error.code, "cancelled")
