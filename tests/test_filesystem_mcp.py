"""Filesystem policy checks use disposable directories, never real user data."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[1] / "integrations" / "coda-filesystem" / "server.py"
spec = importlib.util.spec_from_file_location("coda_filesystem_test", SERVER)
filesystem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(filesystem)


class FilesystemMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "allowed"
        self.root.mkdir()
        self.config_path = self.base / "config.json"
        guard = patch.object(filesystem, "CONFIG_PATH", self.config_path)
        guard.start()
        self.addCleanup(guard.stop)
        self.configure()

    def configure(self, **changes):
        read = {"allowed_roots": [str(self.root)], "blocked_paths": ["**/.ssh-test", "**/.env"], **changes}
        self.config_path.write_text(json.dumps({"read": read}), encoding="utf-8")

    def code(self, path):
        return filesystem.list_directory(str(path)).structured_content["error"]["code"]

    def test_allowed_listing_filters_blocked_entries_and_returns_structured_data(self):
        (self.root / "main.py").touch()
        (self.root / "tests").mkdir()
        (self.root / ".env").touch()
        (self.root / ".ssh-test").mkdir()
        result = filesystem.list_directory(str(self.root))
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["entries"], [
            {"name": "main.py", "type": "file", "modified_at": (self.root / "main.py").stat().st_mtime},
            {"name": "tests", "type": "directory", "modified_at": (self.root / "tests").stat().st_mtime}])
        self.assertIn("[FILE] main.py", result.content[0].text)

    def test_real_modification_times_drive_speech_while_display_stays_alphabetical(self):
        from ai.tools.models import ToolResult
        from ai.tools.presentation import prepare_tool_response

        for name, timestamp in (("a-old.py", 1000), ("b-new.py", 4000),
                                ("c-next.py", 3000), ("d-third.py", 2000)):
            path = self.root / name
            path.touch()
            os.utime(path, (timestamp, timestamp))
        result = filesystem.list_directory(str(self.root))
        entries = result.structured_content["entries"]
        self.assertEqual([entry["modified_at"] for entry in entries], [1000, 4000, 3000, 2000])
        self.assertEqual(result.content[0].text.splitlines()[0], "[FILE] a-old.py")
        speech = prepare_tool_response(ToolResult(True, result.model_dump(mode="json", by_alias=True)),
                                       question="What have I edited recently?")
        self.assertNotIn("a-old.py", speech)
        self.assertLess(speech.index("b-new.py"), speech.index("c-next.py"))
        self.assertLess(speech.index("c-next.py"), speech.index("d-third.py"))

    def test_requested_and_ancestor_blocked_paths_are_denied(self):
        blocked = self.root / ".ssh-test"
        blocked.mkdir()
        child = blocked / "child"
        child.mkdir()
        self.assertEqual(self.code(blocked), "blocked_path")
        self.assertEqual(self.code(child), "blocked_path")

    def test_sibling_prefix_empty_roots_and_traversal_do_not_grant_access(self):
        sibling = self.base / "allowed-other"
        sibling.mkdir()
        self.assertEqual(self.code(sibling), "outside_allowed_roots")
        self.assertEqual(self.code(self.root / ".."), "outside_allowed_roots")
        self.configure(allowed_roots=[])
        self.assertEqual(self.code(self.root), "outside_allowed_roots")

    def test_missing_config_malformed_config_and_relative_roots_fail_closed(self):
        self.config_path.unlink()
        self.assertEqual(self.code(self.root), "configuration_error")
        for text in ("{", "null", '{"read": {}}', '{"read":{"allowed_roots":["."],"blocked_paths":[]}}'):
            self.config_path.write_text(text, encoding="utf-8")
            self.assertEqual(self.code(self.root), "configuration_error")

    def test_empty_missing_file_and_relative_input_are_distinct(self):
        self.assertEqual(filesystem.list_directory(str(self.root)).structured_content["entries"], [])
        self.assertEqual(self.code(self.root / "missing"), "not_found")
        file = self.root / "main.py"
        file.touch()
        self.assertEqual(self.code(file), "not_a_directory")
        self.assertEqual(self.code("relative"), "invalid_arguments")

    def test_links_cannot_escape_roots_or_hide_blocked_names_or_targets(self):
        outside = self.base / "outside"
        outside.mkdir()
        link = self.root / "link"
        blocked = self.root / ".ssh-test"
        blocked.mkdir()
        try:
            link.symlink_to(outside, target_is_directory=True)
            (self.root / "alias").symlink_to(blocked, target_is_directory=True)
        except OSError:
            self.skipTest("Symbolic links unavailable")
        self.assertEqual(self.code(link), "outside_allowed_roots")
        self.assertEqual(self.code(self.root / "alias"), "blocked_path")
        self.assertEqual(filesystem.list_directory(str(self.root)).structured_content["entries"], [])

    def read_code(self, path):
        result = filesystem.read_file(str(path))
        self.assertTrue(result.is_error)
        return result.structured_content["error"]["code"]

    def test_file_read_preserves_utf8_and_universal_newlines(self):
        source = self.root / "notes.txt"
        source.write_bytes("Python 3.11 / 3.12 — café\r\nnext\rlast\n".encode("utf-8"))
        result = filesystem.read_file(str(source))
        self.assertFalse(result.is_error)
        self.assertEqual(result.content[0].text, "Python 3.11 / 3.12 — café\nnext\nlast\n")
        self.assertEqual(result.structured_content, {"kind": "file_contents"})
        source.write_bytes(b"")
        self.assertEqual(filesystem.read_file(str(source)).content[0].text, "")

    def test_file_read_limit_is_bytes_and_accepts_exact_limit(self):
        source = self.root / "large.txt"
        source.write_bytes(b"x" * (1024 * 1024))
        result = filesystem.read_file(str(source))
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.content[0].text), 1024 * 1024)
        source.write_bytes(b"x" * (1024 * 1024 + 1))
        self.assertEqual(self.read_code(source), "file_too_large")
        source.write_bytes("é".encode("utf-8") * (600 * 1024))
        self.assertEqual(self.read_code(source), "file_too_large")

    def test_file_growth_after_stat_cannot_bypass_bounded_read(self):
        source = self.root / "growing.txt"
        source.write_bytes(b"x" * (1024 * 1024 + 1))
        # Simulate a small stat result followed by growth before the actual read.
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)):
            with self.assertRaisesRegex(ValueError, "too big"):
                filesystem.read_safe_file(source)

    def test_file_read_distinguishes_invalid_encoding_missing_and_directory(self):
        source = self.root / "binary.bin"
        source.write_bytes(b"\xff\xfe")
        self.assertEqual(self.read_code(source), "invalid_encoding")
        self.assertEqual(self.read_code(self.root / "missing.txt"), "file_not_found")
        self.assertEqual(self.read_code(self.root), "is_a_directory")
        for path in ("", "relative.txt", "../outside.txt"):
            self.assertEqual(self.read_code(path), "invalid_arguments")

    def test_file_read_respects_roots_blocked_ancestors_and_revocation(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        self.assertEqual(self.read_code(outside), "outside_allowed_roots")
        blocked = self.root / ".ssh-test"
        blocked.mkdir()
        source = blocked / "nested.txt"
        source.write_text("private", encoding="utf-8")
        self.assertEqual(self.read_code(source), "blocked_path")
        source = self.root / "allowed.txt"
        source.write_text("allowed", encoding="utf-8")
        self.assertFalse(filesystem.read_file(str(source)).is_error)
        self.configure(allowed_roots=[])
        self.assertEqual(self.read_code(source), "outside_allowed_roots")

    def test_file_read_missing_and_malformed_config_fail_closed(self):
        source = self.root / "notes.txt"
        source.write_text("private", encoding="utf-8")
        self.config_path.unlink()
        self.assertEqual(self.read_code(source), "configuration_error")
        for text in ("{", "null", '{"read": {}}'):
            self.config_path.write_text(text, encoding="utf-8")
            self.assertEqual(self.read_code(source), "configuration_error")

    def test_file_read_open_failure_returns_error_without_partial_data(self):
        source = self.root / "notes.txt"
        source.write_text("private", encoding="utf-8")
        with patch.object(filesystem, "read_safe_file", side_effect=PermissionError("unavailable")):
            self.assertEqual(self.read_code(source), "os_error")

    def test_file_read_links_cannot_escape_or_hide_blocked_names(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        link = self.root / "alias.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Symbolic links unavailable")
        self.assertEqual(self.read_code(link), "outside_allowed_roots")
        link.unlink()
        inside = self.root / "inside.txt"
        inside.write_text("inside", encoding="utf-8")
        (self.root / ".env").symlink_to(inside)
        self.assertEqual(self.read_code(self.root / ".env"), "blocked_path")
