"""MCP configuration discovery never starts configured executables."""

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai.mcp.config import LocalServerDefinition, discover_definitions


class MCPConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write(self, name, value):
        (self.directory / name).write_text(json.dumps(value), encoding="utf-8")

    def test_independent_definitions_load_without_starting_processes(self):
        self.write("one.json", {"id": "one", "command": sys.executable})
        self.write("two.json", {"id": "two", "command": sys.executable})
        self.write("ignored.json.example", {"id": "ignored", "command": "invalid"})

        with patch("subprocess.Popen") as spawn:
            found = discover_definitions(self.directory)

        self.assertEqual([server.name for server in found.servers], ["one", "two"])
        self.assertFalse(found.servers[0].trusted)
        self.assertEqual(found.servers[0].allowed_tools, frozenset())
        self.assertEqual(found.errors, {})
        spawn.assert_not_called()

    def test_bad_file_does_not_prevent_other_servers_loading(self):
        self.write("good.json", {"id": "good", "command": sys.executable})
        (self.directory / "bad.json").write_text("secret-token not json", encoding="utf-8")

        found = discover_definitions(self.directory)

        self.assertEqual(len(found.servers), 1)
        self.assertEqual(found.errors["bad.json"].code, "configuration_error")
        self.assertNotIn("secret-token", repr(found.errors))

    def test_duplicate_ids_do_not_replace_the_first_definition(self):
        self.write("a.json", {"id": "one", "command": "first"})
        self.write("b.json", {"id": "one", "command": "second"})

        found = discover_definitions(self.directory)

        self.assertEqual(found.servers[0].command, "first")
        self.assertIn("b.json", found.errors)

    def test_secret_references_are_resolved_only_when_connecting(self):
        self.write("one.json", {
            "id": "one", "command": sys.executable,
            "env": {"SERVER_KEY": "CODA_TEST_KEY"},
        })
        definition = discover_definitions(self.directory).servers[0]

        with patch.dict(os.environ, {"CODA_TEST_KEY": "private-value"}):
            self.assertEqual(definition.environment(), {"SERVER_KEY": "private-value"})
        self.assertNotIn("private-value", repr(definition))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "missing"):
                definition.environment()

    def test_invalid_settings_fail_safely(self):
        base = {"id": "one", "command": sys.executable}
        for change in (
            {"transport": "http"}, {"trusted": "true"}, {"timeout_seconds": 0},
            {"timeout_seconds": float("nan")}, {"args": "--help"},
            {"env": {"KEY": "sk-secret-value"}}, {"allowed_tools": ["echo", "echo"]},
            {"cwd": "relative"}, {"max_output_bytes": True}, {"unknown": "field"},
        ):
            with self.subTest(change=change):
                self.write("one.json", base | change)
                found = discover_definitions(self.directory)
                self.assertEqual(found.servers, ())
                self.assertIn("one.json", found.errors)

    def test_missing_directory_is_empty(self):
        self.assertEqual(discover_definitions(self.directory / "missing").servers, ())

    def test_duplicate_json_trust_keys_are_rejected(self):
        (self.directory / "one.json").write_text(
            '{"id":"one","command":"python","trusted":false,"trusted":true}',
            encoding="utf-8",
        )

        found = discover_definitions(self.directory)

        self.assertEqual(found.servers, ())
        self.assertIn("one.json", found.errors)

    def test_non_directory_configuration_path_is_reported(self):
        path = self.directory / "file"
        path.write_text("file", encoding="utf-8")

        self.assertIn("directory", discover_definitions(path).errors)

    def test_environment_can_select_configuration_directory(self):
        self.write("one.json", {"id": "one", "command": sys.executable})
        with patch.dict(os.environ, {"CODA_MCP_CONFIG_DIR": str(self.directory)}):
            self.assertEqual(len(discover_definitions().servers), 1)

    def test_direct_definition_construction_also_validates(self):
        definition = LocalServerDefinition("one", sys.executable)
        for change in ({"trusted": 1}, {"args": ["bad"]}, {"name": "../bad"}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    replace(definition, **change)
