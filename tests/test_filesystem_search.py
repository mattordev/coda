"""Bounded filename traversal uses disposable roots and current read grants."""

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[1] / "integrations/coda-filesystem/server.py"
spec = importlib.util.spec_from_file_location("coda_filesystem_search_test", SERVER)
filesystem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(filesystem)


class FilesystemSearchTests(unittest.TestCase):
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
        self.permissions.write_text(json.dumps({"read": {
            "allowed_roots": [str(self.root)], "blocked_paths": ["**/.env", "**/.ssh"], **changes,
        }}), encoding="utf-8")

    def result(self, pattern="*", **limits):
        result = filesystem.search_files(str(self.root), pattern, **limits)
        self.assertFalse(result.is_error, result)
        return result

    def paths(self, result):
        return [Path(match["path"]).relative_to(self.root).as_posix()
                for match in result.structured_content["matches"]]

    def test_globs_exact_names_case_and_recursive_order_are_deterministic(self):
        (self.root / "b.md").touch()
        (self.root / "A.MD").touch()
        (self.root / "README").touch()
        child = self.root / "sub"
        child.mkdir()
        (child / "c.md").touch()
        (child / "binary.dat").write_bytes(b"\xff\x00")
        self.assertEqual(self.paths(self.result("*.md")), ["A.MD", "b.md", "sub/c.md"])
        self.assertEqual(self.paths(self.result("readme")), ["README"])
        self.assertFalse(self.result("*.md").structured_content["partial"])

    def test_depth_caps_are_explicit_and_only_files_are_matches(self):
        child = self.root / "folder.md"
        child.mkdir()
        (child / "inside.md").touch()
        shallow = self.result("*.md", max_depth=0)
        self.assertEqual(self.paths(shallow), [])
        self.assertEqual(shallow.structured_content["limits_reached"], ["depth_limit"])
        full = self.result("*.md", max_depth=1)
        self.assertEqual(self.paths(full), ["folder.md/inside.md"])
        self.assertFalse(full.structured_content["partial"])

    def test_result_cap_returns_sorted_matches_and_discloses_partiality(self):
        for name in ("c.md", "a.md", "b.md"):
            (self.root / name).touch()
        result = self.result("*.md", max_results=2)
        self.assertEqual(self.paths(result), ["a.md", "b.md"])
        self.assertIn("result_limit", result.structured_content["limits_reached"])
        self.assertIn("partial", result.content[0].text)

    def test_entry_cap_does_not_select_an_arbitrary_directory_prefix(self):
        for name in ("a.md", "b.md", "c.md"):
            (self.root / name).touch()
        result = self.result("*.md", max_entries=2)
        self.assertEqual(self.paths(result), [])
        self.assertEqual(result.structured_content["entries_examined"], 3)
        self.assertEqual(result.structured_content["limits_reached"], ["entry_limit"])

    def test_output_and_deadline_limits_are_bounded_partial_results(self):
        (self.root / "notes.md").touch()
        with patch.object(filesystem, "SEARCH_OUTPUT_CHARS", 1):
            result = self.result("*.md")
        self.assertEqual(self.paths(result), [])
        self.assertIn("output_limit", result.structured_content["limits_reached"])
        with patch.object(filesystem, "monotonic", side_effect=[0, 6]):
            result = self.result("*.md")
        self.assertEqual(self.paths(result), [])
        self.assertIn("time_limit", result.structured_content["limits_reached"])

    def test_blocks_outside_roots_and_missing_paths_use_existing_policy(self):
        blocked = self.root / ".ssh"
        blocked.mkdir()
        (blocked / "private.md").touch()
        (self.root / ".env").touch()
        (self.root / "public.md").touch()
        self.assertEqual(self.paths(self.result()), ["public.md"])
        for target, expected in ((self.base, "outside_allowed_roots"),
                                 (blocked, "blocked_path"), (self.root / "missing", "not_found"),
                                 (self.root / "public.md", "not_a_directory")):
            result = filesystem.search_files(str(target), "*")
            self.assertEqual(result.structured_content["error"]["code"], expected)

    def test_malformed_limits_and_patterns_do_not_enumerate(self):
        bad = [{"path": "."}, {"path": None}, {"pattern": ""}, {"pattern": "**/*.md"},
               {"pattern": "..\\*"}, {"pattern": "a\0b"}, {"pattern": "x" * 257},
               {"max_depth": -1}, {"max_depth": 17}, {"max_depth": True},
               {"max_entries": 0}, {"max_entries": 10001}, {"max_entries": "5"},
               {"max_results": 0}, {"max_results": 201}, {"max_results": 1.5}]
        with patch.object(filesystem.os, "scandir", side_effect=AssertionError("Validate first")):
            for changes in bad:
                with self.subTest(changes=changes):
                    arguments = {"path": str(self.root), "pattern": "*", **changes}
                    result = filesystem.search_files(**arguments)
                    self.assertEqual(result.structured_content["error"]["code"], "invalid_arguments")

    def test_empty_and_no_match_results_are_complete_within_authorised_scope(self):
        self.assertFalse(self.result().structured_content["partial"])
        (self.root / "notes.txt").touch()
        result = self.result("*.md")
        self.assertEqual(self.paths(result), [])
        self.assertFalse(result.structured_content["partial"])

    def test_policy_revocation_stops_enumeration_and_discards_observations(self):
        for index in range(80):
            (self.root / f"{index:03}.md").touch()
        original = filesystem._search_policy
        calls = 0

        def revoke(config, root):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.configure(allowed_roots=[])
            return original(config, root)

        with patch.object(filesystem, "_search_policy", side_effect=revoke):
            result = filesystem.search_files(str(self.root), "*.md")
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["error"]["code"], "permission_denied")
        self.assertNotIn("matches", result.structured_content)
        self.assertEqual(calls, 2)

    def test_iterator_is_closed_on_early_limit_without_reading_contents(self):
        for name in ("a.md", "b.md"):
            (self.root / name).touch()
        real_scandir = os.scandir
        real_open = Path.open
        closed = []

        def configuration_only(path, *args, **kwargs):
            self.assertEqual(path.resolve(), self.permissions.resolve(), "No file contents may be opened.")
            return real_open(path, *args, **kwargs)

        class ObservedScan:
            def __init__(self, path):
                self.iterator = real_scandir(path)

            def __enter__(self):
                return self.iterator

            def __exit__(self, *unused):
                self.iterator.close()
                closed.append(True)

        with patch.object(filesystem.os, "scandir", side_effect=ObservedScan), \
             patch.object(Path, "open", configuration_only):
            result = self.result(max_entries=1)
        self.assertTrue(result.structured_content["partial"])
        self.assertEqual(closed, [True])

    def test_concurrent_searches_do_not_share_results_or_counters(self):
        from concurrent.futures import ThreadPoolExecutor

        (self.root / "notes.md").touch()
        (self.root / "notes.txt").touch()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda pattern: filesystem.search_files(str(self.root), pattern),
                                       ("*.md", "*.txt")))
        self.assertEqual(self.paths(results[0]), ["notes.md"])
        self.assertEqual(self.paths(results[1]), ["notes.txt"])
        self.assertEqual([r.structured_content["entries_examined"] for r in results], [2, 2])

    def test_missing_and_invalid_configuration_fail_closed(self):
        self.permissions.unlink()
        for text in (None, "{", "null"):
            if text is not None:
                self.permissions.write_text(text, encoding="utf-8")
            result = filesystem.search_files(str(self.root), "*")
            self.assertEqual(result.structured_content["error"]["code"], "configuration_error")

    def test_unavailable_subdirectory_is_reported_as_partial(self):
        child = self.root / "child"
        child.mkdir()
        (self.root / "visible.md").touch()
        real_scandir = os.scandir

        def inaccessible(path):
            if path == child:
                raise PermissionError
            return real_scandir(path)

        with patch.object(filesystem.os, "scandir", side_effect=inaccessible):
            result = self.result("*.md")
        self.assertEqual(self.paths(result), ["visible.md"])
        self.assertIn("unavailable_directory", result.structured_content["limits_reached"])

    def test_symlinks_do_not_escape_duplicate_results_or_loop(self):
        inside = self.root / "inside"
        inside.mkdir()
        (inside / "public.md").touch()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private.md").touch()
        try:
            (self.root / "alias").symlink_to(inside, target_is_directory=True)
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
            (inside / "cycle").symlink_to(self.root, target_is_directory=True)
            (self.root / "alias.md").symlink_to(inside / "public.md")
        except OSError:
            self.skipTest("Native symbolic links unavailable")
        self.assertEqual(self.paths(self.result("*.md")), ["inside/public.md"])

    @unittest.skipUnless(os.name == "nt", "Native Windows junction semantics")
    def test_junctions_are_not_followed_even_to_another_allowed_directory(self):
        inside = self.root / "inside"
        inside.mkdir()
        (inside / "public.md").touch()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private.md").touch()
        self.configure(allowed_roots=[str(self.root), str(outside)])
        links = []
        try:
            for name, target in (("alias", inside), ("escape", outside), ("cycle", self.root)):
                link = self.root / name
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                        capture_output=True, text=True, check=False)
                if result.returncode:
                    self.skipTest("Junction creation unavailable")
                links.append(link)
            self.assertEqual(self.paths(self.result("*.md")), ["inside/public.md"])
        finally:
            for link in links:
                self.assertTrue(link.resolve().is_relative_to(self.base))
                link.rmdir()

    def test_search_speech_preserves_partiality_and_does_not_claim_a_listing(self):
        from ai.tools.models import ToolResult
        from ai.tools.presentation import prepare_tool_response

        for name in ("a.md", "b.md"):
            (self.root / name).touch()
        result = self.result("*.md", max_results=1)
        speech = prepare_tool_response(ToolResult(True, result.model_dump(mode="json", by_alias=True)))
        self.assertIn("filename search", speech)
        self.assertIn("1 matching file", speech)
        self.assertIn("partial", speech)
        self.assertIn("a.md", speech)
        self.assertNotIn("listing", speech)

    def test_long_search_results_do_not_trigger_unnecessary_model_interpretation(self):
        from ai.tools.interpretation import interpret_result
        from ai.tools.models import ToolResult

        for index in range(12):
            (self.root / f"report-{index}.md").touch()
        raw = self.result("*.md", max_results=10)
        result = ToolResult(True, raw.model_dump(mode="json", by_alias=True))
        with patch.dict(os.environ, {"CODA_MCP_INTERPRETATION": "auto"}), \
             patch("ai.llm_router.core.get_provider_order", side_effect=AssertionError("No model is needed")):
            interpreted = interpret_result(result, "Find Markdown files")
        self.assertFalse(interpreted.used_llm)
        self.assertIn("10 matching files", interpreted.text)
        self.assertIn("partial", interpreted.text)

    def test_real_search_discovery_grants_cancellation_and_revocation(self):
        from ai.intents import IntentRequest
        from ai.mcp.config import LocalServerDefinition
        from ai.mcp.runtime import LocalMCPManager

        (self.root / "notes.md").touch()
        for mode in ("operation", "persistent"):
            with self.subTest(mode=mode):
                self.configure()
                definition = LocalServerDefinition("search", sys.executable,
                    args=(str(SERVER), "--config", str(self.permissions)), trusted=True,
                    allowed_tools=frozenset({"list_directory"}), session_mode=mode)
                manager = LocalMCPManager([definition])
                try:
                    found = manager.discover("search")
                    self.assertTrue(found.success, found.error)
                    intent = next(i for i in found.data if i.name == "mcp.search.search_files")
                    request = IntentRequest(intent, "Find Markdown files", 1,
                                            arguments={"path": str(self.root), "pattern": "*.md"})
                    self.assertEqual(manager.executor(intent.name).execute(request).error.code, "permission_denied")
                    manager.replace_definition(replace(definition, allowed_tools=frozenset({"search_files"})))
                    found = manager.discover("search")
                    intent = next(i for i in found.data if i.name == "mcp.search.search_files")
                    request = replace(request, intent=intent)
                    result = manager.executor(intent.name).execute(request)
                    self.assertTrue(result.success, result.error)
                    self.assertEqual(result.data["structuredContent"]["matches"][0]["name"], "notes.md")
                    cancelled = Event()
                    cancelled.set()
                    self.assertEqual(manager.executor(intent.name).execute(request, cancel_event=cancelled).error.code, "cancelled")
                    invalid = replace(request, arguments={**request.arguments, "max_entries": True})
                    self.assertEqual(manager.executor(intent.name).execute(invalid).error.code, "invalid_arguments")
                    invalid = replace(request, arguments={**request.arguments, "max_results": 0})
                    denied = manager.executor(intent.name).execute(invalid)
                    self.assertFalse(denied.success)
                    self.assertEqual(denied.data["structuredContent"]["error"]["code"], "invalid_arguments")
                    self.configure(allowed_roots=[])
                    denied = manager.executor(intent.name).execute(request)
                    self.assertFalse(denied.success)
                    self.assertEqual(denied.data["structuredContent"]["error"]["code"], "outside_allowed_roots")
                finally:
                    self.assertTrue(manager.close()["search"].success)

    def test_cancelling_an_active_scan_reaps_the_real_server(self):
        import psutil
        from ai.intents import IntentRequest
        from ai.mcp.config import LocalServerDefinition
        from ai.mcp.runtime import LocalMCPManager

        marker = self.base / "scan-started"
        wrapper = self.base / "server-wrapper.py"
        # Pause only the real search's first directory access. The marker proves
        # cancellation occurs during traversal, without timing a fast real scan.
        wrapper.write_text('''
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('search_cancel_fixture', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.CONFIG_PATH = Path(sys.argv[2])
original_scandir = module.os.scandir
def paused_scan(path):
    if Path(path).resolve() == Path(sys.argv[3]):
        marker = Path(sys.argv[4])
        pending = marker.with_suffix('.pending')
        pending.write_text(str(os.getpid()), encoding='utf-8')
        pending.replace(marker)
        time.sleep(60)
    return original_scandir(path)
module.os.scandir = paused_scan
module.server.run(transport='stdio')
''', encoding="utf-8")
        definition = LocalServerDefinition("active", sys.executable,
            args=(str(wrapper), str(SERVER), str(self.permissions), str(self.root), str(marker)),
            trusted=True, allowed_tools=frozenset({"search_files"}), session_mode="persistent",
            startup_timeout_seconds=15, timeout_seconds=10)
        manager = LocalMCPManager([definition])
        cancelled = Event()
        results = []
        worker = None
        try:
            found = manager.discover("active")
            self.assertTrue(found.success, found.error)
            intent = next(i for i in found.data if i.name == "mcp.active.search_files")
            request = IntentRequest(intent, "Search", 1, arguments={"path": str(self.root), "pattern": "*"})
            worker = Thread(target=lambda: results.append(manager.executor(intent.name).execute(
                request, cancel_event=cancelled)), daemon=True)
            worker.start()
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                cancelled.wait(0.01)
            self.assertTrue(marker.exists(), "The scan must start before cancellation.")
            pid = int(marker.read_text(encoding="utf-8"))
            cancelled.set()
            worker.join(12)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results[0].error.code, "cancelled")
            self.assertTrue(manager.close()["active"].success)
            # Windows job termination becomes observable asynchronously. Match
            # the existing MCP lifecycle tests' bounded process-exit wait.
            exit_deadline = time.monotonic() + 5
            while psutil.pid_exists(pid) and time.monotonic() < exit_deadline:
                time.sleep(0.01)
            self.assertFalse(psutil.pid_exists(pid), "The cancelled server must be reaped.")
        finally:
            cancelled.set()
            manager.close()
            if worker is not None:
                worker.join(12)
