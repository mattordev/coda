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

    def range_text(self, result):
        """Remove only the server's explicit scope notice, preserving source text."""
        self.assertFalse(result.is_error, result)
        text = result.content[0].text
        return text.split("\n", 1)[1] if result.structured_content["partial"] else text

    def test_large_file_ranges_seek_directly_and_bound_actual_io(self):
        from unittest.mock import MagicMock

        source = self.root / "large.txt"
        source.write_bytes(b"a" * (2 * 1024 * 1024) + b"tail")
        self.assertEqual(self.read_code(source), "file_too_large")
        real_open = Path.open
        reads = []

        def tracked_open(path, mode="r", *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            if path == source and mode == "rb":
                tracked = MagicMock(wraps=handle)
                tracked.__enter__.return_value = tracked
                tracked.__exit__.side_effect = lambda *unused: handle.close()
                reads.append(tracked)
                return tracked
            return handle

        with patch.object(Path, "open", tracked_open), \
             patch.object(filesystem, "read_safe_file", side_effect=AssertionError("No whole-file read")):
            result = filesystem.read_file(str(source), offset_bytes=2 * 1024 * 1024, max_bytes=4)
        self.assertEqual(self.range_text(result), "tail")
        self.assertTrue(result.structured_content["partial"])
        self.assertFalse(result.structured_content["has_more"])
        self.assertIsNone(result.structured_content["next_offset_bytes"])
        reads[0].read.assert_called_once_with(5)
        self.assertEqual(reads[0].seek.call_args_list[-1].args, (2 * 1024 * 1024,))
        self.assertTrue(reads[0].closed)

    def test_range_continuations_preserve_multibyte_text_and_newlines(self):
        source = self.root / "unicode.txt"
        raw = "a\u00e9\U0001f680\r\nnext\rlast\n".encode("utf-8")
        source.write_bytes(raw)
        for budget in range(4, 11):
            with self.subTest(budget=budget):
                offset, pieces = 0, []
                while offset is not None:
                    result = filesystem.read_file(str(source), offset_bytes=offset, max_bytes=budget)
                    pieces.append(self.range_text(result))
                    metadata = result.structured_content
                    self.assertLessEqual(metadata["bytes_read"], budget)
                    following = metadata["next_offset_bytes"]
                    if following is not None:
                        self.assertGreater(following, offset)
                        self.assertEqual(following, offset + metadata["bytes_read"])
                        self.assertTrue(metadata["has_more"])
                    offset = following
                self.assertEqual("".join(pieces), "a\u00e9\U0001f680\nnext\nlast\n")

    def test_ranges_disclose_omitted_prefix_and_suffix(self):
        source = self.root / "notes.txt"
        source.write_bytes(b"abcdef")
        result = filesystem.read_file(str(source), max_bytes=3)
        self.assertEqual(self.range_text(result), "abc")
        self.assertEqual(result.structured_content, {
            "kind": "file_contents", "partial": True, "offset_bytes": 0,
            "bytes_read": 3, "has_more": True, "next_offset_bytes": 3,
        })
        self.assertIn("Partial file content", result.content[0].text)
        self.assertIn("Continue at byte 3", result.content[0].text)
        final = filesystem.read_file(str(source), offset_bytes=3)
        self.assertEqual(self.range_text(final), "def")
        self.assertTrue(final.structured_content["partial"])
        self.assertFalse(final.structured_content["has_more"])
        self.assertIn("End of file", final.content[0].text)
        complete = filesystem.read_file(str(source), max_bytes=6)
        self.assertFalse(complete.structured_content["partial"])
        self.assertEqual(self.range_text(complete), "abcdef")

    def test_range_default_budget_and_exact_maximum_are_bounded(self):
        source = self.root / "large.txt"
        source.write_bytes(b"x" * (2 * filesystem.MAX_READ_BYTES))
        default = filesystem.read_file(str(source), offset_bytes=1)
        self.assertEqual(default.structured_content["bytes_read"], 65536)
        maximum = filesystem.read_file(str(source), max_bytes=filesystem.MAX_READ_BYTES)
        self.assertEqual(maximum.structured_content["bytes_read"], filesystem.MAX_READ_BYTES)
        self.assertTrue(maximum.structured_content["has_more"])

    def test_invalid_range_arguments_fail_without_opening_file(self):
        source = self.root / "notes.txt"
        source.write_bytes(b"valid")
        for arguments in ({"offset_bytes": -1}, {"offset_bytes": True}, {"offset_bytes": 1.5},
                          {"offset_bytes": "1"}, {"offset_bytes": 2**63},
                          {"max_bytes": 0}, {"max_bytes": -1}, {"max_bytes": True},
                          {"max_bytes": 1.5}, {"max_bytes": "1"},
                          {"max_bytes": filesystem.MAX_READ_BYTES + 1}):
            with self.subTest(arguments=arguments), \
                 patch.object(filesystem, "load_config", side_effect=AssertionError("Validate first")):
                result = filesystem.read_file(str(source), **arguments)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["code"], "invalid_arguments")

    def test_empty_file_eof_and_beyond_eof_have_explicit_range_semantics(self):
        source = self.root / "empty.txt"
        source.touch()
        self.assertEqual(self.range_text(filesystem.read_file(str(source), max_bytes=1)), "")
        source.write_bytes(b"abc")
        at_end = filesystem.read_file(str(source), offset_bytes=3, max_bytes=1)
        self.assertEqual(self.range_text(at_end), "")
        self.assertTrue(at_end.structured_content["partial"])
        beyond = filesystem.read_file(str(source), offset_bytes=4, max_bytes=1)
        self.assertEqual(beyond.structured_content["error"]["code"], "invalid_arguments")

    def test_range_rejects_split_start_tiny_budget_and_malformed_utf8(self):
        source = self.root / "unicode.txt"
        source.write_bytes("\u00e9\r\nx".encode("utf-8"))
        for arguments in ({"offset_bytes": 1, "max_bytes": 4}, {"max_bytes": 1},
                          {"offset_bytes": 2, "max_bytes": 1}):
            result = filesystem.read_file(str(source), **arguments)
            self.assertEqual(result.structured_content["error"]["code"], "invalid_arguments")
        for raw in (b"\xffbad", b"\xc3", b"x\xc3z"):
            source.write_bytes(raw)
            result = filesystem.read_file(str(source), max_bytes=8)
            self.assertEqual(result.structured_content["error"]["code"], "invalid_encoding")

    def test_ranges_use_current_roots_blocks_and_error_policy(self):
        source = self.root / "notes.txt"
        source.write_bytes(b"facts")
        self.assertFalse(filesystem.read_file(str(source), max_bytes=2).is_error)
        self.configure(allowed_roots=[])
        result = filesystem.read_file(str(source), max_bytes=2)
        self.assertEqual(result.structured_content["error"]["code"], "outside_allowed_roots")
        self.configure(blocked_paths=["**/notes.txt"])
        result = filesystem.read_file(str(source), max_bytes=2)
        self.assertEqual(result.structured_content["error"]["code"], "blocked_path")
        self.configure()
        with patch.object(filesystem, "read_file_range", side_effect=PermissionError("denied")):
            result = filesystem.read_file(str(source), max_bytes=2)
        self.assertEqual(result.structured_content["error"]["code"], "os_error")

    def test_partial_ranges_are_recognised_by_existing_interpretation_policy(self):
        from ai.tools.interpretation import _partial_source

        source = self.root / "notes.txt"
        source.write_bytes(b"some observed facts")
        result = filesystem.read_file(str(source), max_bytes=4)
        self.assertTrue(_partial_source(result.model_dump(mode="json", by_alias=True)))

    def test_concurrent_ranges_keep_offsets_and_contents_independent(self):
        from concurrent.futures import ThreadPoolExecutor

        source = self.root / "concurrent.txt"
        raw = b"first " + b"a" * 1000 + b" last"
        source.write_bytes(raw)
        offsets = (0, 2, 500, 1006)
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda offset: filesystem.read_file(
                str(source), offset_bytes=offset, max_bytes=4), offsets))
        for offset, result in zip(offsets, results):
            self.assertEqual(self.range_text(result), raw[offset:offset + 4].decode("utf-8"))
            self.assertEqual(result.structured_content["offset_bytes"], offset)
