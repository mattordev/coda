"""Metadata observations respect read policy without opening file contents."""

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[1] / "integrations/coda-filesystem/server.py"
spec = importlib.util.spec_from_file_location("coda_filesystem_metadata_test", SERVER)
filesystem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(filesystem)


class FilesystemMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "allowed"
        self.root.mkdir()
        self.permissions = self.base / "permissions.json"
        guard = patch.object(filesystem, "CONFIG_PATH", self.permissions)
        guard.start()
        self.addCleanup(guard.stop)
        self.configure()

    def configure(self, **changes):
        policy = {"read": {"allowed_roots": [str(self.root)],
                           "blocked_paths": ["**/.env", "**/.ssh"], **changes}}
        self.permissions.write_text(json.dumps(policy), encoding="utf-8")
        return policy

    def code(self, path):
        result = filesystem.get_file_metadata(str(path))
        self.assertTrue(result.is_error)
        return result.structured_content["error"]["code"]

    def test_file_metadata_uses_bytes_and_real_modification_time(self):
        source = self.root / "notes.txt"
        source.write_bytes("caf\u00e9".encode("utf-8"))
        os.utime(source, (1234567890, 1234567890))
        result = filesystem.get_file_metadata(str(source))
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, {
            "kind": "file_metadata", "path": str(source), "type": "file",
            "size_bytes": 5, "modified_at": source.stat().st_mtime,
        })
        self.assertIn("5 bytes", result.content[0].text)
        self.assertIn("+00:00", result.content[0].text)
        self.assertNotIn("created_at", result.structured_content)

    def test_directory_size_is_not_misrepresented_as_tree_size(self):
        (self.root / "child").write_bytes(b"data")
        result = filesystem.get_file_metadata(str(self.root))
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["type"], "directory")
        self.assertIsNone(result.structured_content["size_bytes"])
        self.assertNotIn("bytes", result.content[0].text)

    def test_binary_and_large_file_metadata_never_reads_contents(self):
        source = self.root / "binary.dat"
        with source.open("wb") as target:
            target.write(b"\xff")
            target.seek(2 * 1024 * 1024)
            target.write(b"\x00")
        policy = self.configure()
        with patch.object(filesystem, "load_config", return_value=policy), \
             patch.object(Path, "open", side_effect=AssertionError("Metadata must not open contents")):
            result = filesystem.get_file_metadata(str(source))
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["size_bytes"], 2 * 1024 * 1024 + 1)

    def test_outside_blocked_missing_and_revoked_paths_fail_closed(self):
        outside = self.base / "outside"
        outside.touch()
        self.assertEqual(self.code(outside), "outside_allowed_roots")
        blocked = self.root / ".ssh"
        blocked.mkdir()
        (blocked / "key").touch()
        self.assertEqual(self.code(blocked / "key"), "blocked_path")
        self.assertEqual(self.code(self.root / "missing"), "not_found")
        source = self.root / "notes.txt"
        source.touch()
        self.assertFalse(filesystem.get_file_metadata(str(source)).is_error)
        self.configure(allowed_roots=[])
        self.assertEqual(self.code(source), "outside_allowed_roots")

    def test_invalid_arguments_and_configuration_fail_safely(self):
        for path in (None, True, 42, "", "relative", str(self.root / "bad\0name")):
            with self.subTest(path=path):
                result = filesystem.get_file_metadata(path)
                self.assertEqual(result.structured_content["error"]["code"], "invalid_arguments")
        self.permissions.unlink()
        self.assertEqual(self.code(self.root), "configuration_error")
        self.permissions.write_text("{", encoding="utf-8")
        self.assertEqual(self.code(self.root), "configuration_error")

    def test_symlink_cannot_reveal_outside_or_blocked_target_metadata(self):
        outside = self.base / "outside"
        outside.touch()
        blocked = self.root / ".env"
        blocked.touch()
        try:
            (self.root / "outside-link").symlink_to(outside)
            (self.root / "blocked-link").symlink_to(blocked)
        except OSError:
            self.skipTest("Native symbolic links unavailable")
        self.assertEqual(self.code(self.root / "outside-link"), "outside_allowed_roots")
        self.assertEqual(self.code(self.root / "blocked-link"), "blocked_path")

    @unittest.skipUnless(os.name == "nt", "Native Windows junction semantics")
    def test_junction_cannot_reveal_outside_or_blocked_metadata(self):
        import subprocess

        outside = self.base / "outside"
        outside.mkdir()
        blocked = self.root / ".ssh"
        blocked.mkdir()
        for target, code in ((outside, "outside_allowed_roots"), (blocked, "blocked_path")):
            (target / "private").touch()
            link = self.root / ("link-" + code)
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                    capture_output=True, text=True, check=False)
            if result.returncode:
                self.skipTest("Junction creation unavailable")
            try:
                self.assertEqual(self.code(link / "private"), code)
            finally:
                self.assertTrue(link.resolve().is_relative_to(self.base))
                link.rmdir()

    def test_real_mcp_metadata_requires_grant_and_rechecks_current_policy(self):
        from ai.intents import IntentRequest
        from ai.mcp.config import LocalServerDefinition
        from ai.mcp.runtime import LocalMCPManager

        source = self.root / "notes.txt"
        source.write_bytes(b"facts")
        for mode in ("operation", "persistent"):
            with self.subTest(mode=mode):
                self.configure()
                definition = LocalServerDefinition("metadata", sys.executable,
                    args=(str(SERVER), "--config", str(self.permissions)), trusted=True,
                    allowed_tools=frozenset({"read_file"}), session_mode=mode)
                manager = LocalMCPManager([definition])
                try:
                    found = manager.discover("metadata")
                    self.assertTrue(found.success, found.error)
                    intent = next(i for i in found.data if i.name == "mcp.metadata.get_file_metadata")
                    request = IntentRequest(intent, "Inspect file", 1, arguments={"path": str(source)})
                    self.assertEqual(manager.executor(intent.name).execute(request).error.code, "permission_denied")
                    manager.replace_definition(replace(definition, allowed_tools=frozenset({"get_file_metadata"})))
                    found = manager.discover("metadata")
                    intent = next(i for i in found.data if i.name == "mcp.metadata.get_file_metadata")
                    request = replace(request, intent=intent)
                    result = manager.executor(intent.name).execute(request)
                    self.assertTrue(result.success, result.error)
                    self.assertEqual(result.data["structuredContent"]["size_bytes"], 5)
                    cancelled = Event()
                    cancelled.set()
                    self.assertEqual(manager.executor(intent.name).execute(request, cancel_event=cancelled).error.code, "cancelled")
                    self.configure(allowed_roots=[])
                    denied = manager.executor(intent.name).execute(request)
                    self.assertFalse(denied.success)
                    self.assertEqual(denied.data["structuredContent"]["error"]["code"], "outside_allowed_roots")
                finally:
                    self.assertTrue(manager.close()["metadata"].success)
