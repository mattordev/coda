"""Filesystem policy checks use disposable directories, never real user data."""

import importlib.util
import json
from pathlib import Path
import tempfile
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
            {"name": "main.py", "type": "file"}, {"name": "tests", "type": "directory"}])
        self.assertIn("[FILE] main.py", result.content[0].text)

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
