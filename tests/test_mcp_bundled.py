"""Portable bundle selection does not launch/import code or grant authority."""

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai.mcp import bundled
from ai.mcp.config import discover_definitions
from ai.mcp.transport import SDKStdioTransport


class BundledMCPTests(unittest.TestCase):
    def test_launch_is_portable_across_install_roots_and_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ("first install", "second-安装"):
                root = base / name
                script = root / "integrations" / "coda-filesystem" / "server.py"
                script.parent.mkdir(parents=True)
                script.write_text("raise AssertionError('must not import')", encoding="utf-8")
                config_dir = root / "mcp_servers"
                config_dir.mkdir()
                (config_dir / "filesystem.json").write_text(json.dumps({
                    "id": "files", "bundled_server": "filesystem",
                }), encoding="utf-8")
                with patch.object(bundled, "INSTALL_ROOT", root), patch("subprocess.Popen") as launch:
                    found = discover_definitions(config_dir)
                launch.assert_not_called()
                self.assertEqual(found.errors, {})
                definition = found.servers[0]
                self.assertEqual(definition.command, sys.executable)
                self.assertEqual(definition.args, (str(script.resolve()),))
                self.assertFalse(definition.trusted)
                self.assertEqual(definition.allowed_tools, frozenset())
                self.assertIsNone(definition.cwd)

    def test_unknown_ids_and_mixed_launch_settings_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "filesystem.json"
            for change in ({"bundled_server": "../filesystem"}, {"command": "evil"},
                           {"args": ["override"]}, {"cwd": str(Path(directory))},
                           {"bundled_server": None}, {"transport": "streamable-http", "url": "https://example.test"}):
                with self.subTest(change=change):
                    path.write_text(json.dumps({"id": "files", "bundled_server": "filesystem", **change}), encoding="utf-8")
                    found = discover_definitions(directory)
                    self.assertEqual(found.servers, ())
                    self.assertIn("filesystem.json", found.errors)

    def test_missing_or_linked_entrypoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bundled, "INSTALL_ROOT", Path(directory)):
            with self.assertRaises(ValueError):
                bundled.bundled_launch("filesystem")
            script = Path(directory) / "integrations" / "coda-filesystem" / "server.py"
            script.parent.mkdir(parents=True)
            target = Path(directory) / "outside.py"
            target.write_text("", encoding="utf-8")
            try:
                script.symlink_to(target)
            except OSError:
                self.skipTest("Symbolic links unavailable")
            with self.assertRaises(ValueError):
                bundled.bundled_launch("filesystem")

    def test_real_bundled_filesystem_uses_coda_transport_and_structured_feedback(self):
        from ai.tools.presentation import prepare_tool_display, prepare_tool_response

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            allowed.mkdir()
            (allowed / "main.py").write_text("", encoding="utf-8")
            (allowed / ".env").write_text("do-not-read", encoding="utf-8")
            permissions = root / "permissions.json"
            permissions.write_text(json.dumps({"read": {
                "allowed_roots": [str(allowed)], "blocked_paths": ["**/.env"],
            }}), encoding="utf-8")
            config_dir = root / "mcp_servers"
            config_dir.mkdir()
            (config_dir / "filesystem.json").write_text(json.dumps({
                "id": "filesystem", "bundled_server": "filesystem", "trusted": True,
                "allowed_tools": ["list_directory"],
            }), encoding="utf-8")
            definition = discover_definitions(config_dir).servers[0]
            # Override only the fixture settings path, keeping the resolved native
            # interpreter/entrypoint. Production uses the colocated private config.
            definition = replace(definition, args=(*definition.args, "--config", str(permissions)), cwd=directory)
            transport = SDKStdioTransport()
            for mode in ("operation", "persistent"):
                with self.subTest(mode=mode):
                    current = replace(definition, session_mode=mode)
                    try:
                        found = transport.request(current, "discover")
                        self.assertTrue(found.success, found.error)
                        self.assertEqual(found.data["tools"][0]["name"], "list_directory")
                        result = transport.request(current, "invoke", tool_name="list_directory", arguments={"path": str(allowed)})
                        self.assertTrue(result.success, result.error)
                        self.assertEqual(result.data["structuredContent"]["entries"], [{"name": "main.py", "type": "file"}])
                        self.assertNotIn(".env", prepare_tool_display(result))
                        self.assertIn("1 file", prepare_tool_response(result))
                        denied = transport.request(current, "invoke", tool_name="list_directory", arguments={"path": str(root)})
                        self.assertFalse(denied.success)
                        self.assertEqual(denied.error.code, "mcp_tool_error")
                        self.assertIn("allowed locations", prepare_tool_response(denied))
                        blocked = transport.request(current, "invoke", tool_name="list_directory", arguments={"path": str(allowed / ".env")})
                        self.assertIn("blocked", prepare_tool_response(blocked))
                    finally:
                        self.assertTrue(transport.retire("filesystem").success)

    def test_untrusted_and_empty_allowlist_do_not_launch_a_bundle(self):
        from ai.mcp.config import LocalServerDefinition
        command, args = bundled.bundled_launch("filesystem")
        definition = LocalServerDefinition("filesystem", command, args)
        transport = SDKStdioTransport()
        with patch("subprocess.Popen") as launch:
            self.assertEqual(transport.request(definition, "discover").error.code, "permission_denied")
            self.assertEqual(transport.request(replace(definition, trusted=True), "invoke", tool_name="list_directory").error.code, "permission_denied")
        launch.assert_not_called()
