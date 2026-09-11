import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "tool" / "update_sandbox" / "update_sandbox.py"
SPEC = importlib.util.spec_from_file_location("update_sandbox", SCRIPT)
sandboxer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sandboxer)


class UpdateSandboxTests(unittest.TestCase):
    def test_launcher_elapsed_time_uses_estimate_grading(self):
        progress = sandboxer.SandboxProgress("Installing", 100, io.StringIO())
        for elapsed, colour in ((100, "\033[32;1m"), (100.01, "\033[33;1m"),
                                (150, "\033[33;1m"), (150.01, "\033[31;1m")):
            with self.subTest(elapsed=elapsed):
                timing = progress._timing(elapsed)
                self.assertTrue(timing.startswith(colour))
                self.assertIn("elapsed / ~100s estimated", timing)

    def test_launcher_progress_uses_plain_messages_when_redirected(self):
        stream = io.StringIO()
        with sandboxer.SandboxProgress("Installing", 300, stream):
            pass
        self.assertEqual(
            stream.getvalue(),
            "[SANDBOX] Installing...\n[SANDBOX] Installing: done\n",
        )

    def test_launcher_step_hides_process_output_in_log(self):
        with tempfile.TemporaryDirectory(
            prefix="coda-update-sandbox-test-", dir=tempfile.gettempdir()
        ) as directory:
            root = Path(directory)
            log = io.StringIO()
            with patch.object(sandboxer.subprocess, "run") as process:
                sandboxer._run_launcher_step(
                    ["python", "fixture"], "Installing", 10, 20, {}, log, root
                )
            self.assertIs(process.call_args.kwargs["stdout"], log)
            self.assertIs(process.call_args.kwargs["stderr"], sandboxer.subprocess.STDOUT)
            self.assertIn("--- Installing ---", log.getvalue())

    def test_confirmation_prompt_is_always_lowercase(self):
        with patch("builtins.input", return_value="") as prompt:
            self.assertTrue(sandboxer._confirm("Continue?", True))
        self.assertEqual(prompt.call_args.args[0], "Continue? (y/n): ")

    def test_suggested_values_accept_yes_no_or_direct_value(self):
        cases = (
            ("", [], "1.4.3"),
            ("y", [], "1.4.3"),
            ("1.4.2", [], "1.4.2"),
            ("n", ["1.4.1"], "1.4.1"),
        )
        for first, following, expected in cases:
            with self.subTest(first=first), patch(
                "builtins.input", side_effect=[first, *following]
            ):
                self.assertEqual(
                    sandboxer._choose_suggestion("Use version", "1.4.3", "version number"),
                    expected,
                )

    def test_sandbox_must_have_dedicated_name_inside_temp(self):
        with self.assertRaises(ValueError):
            sandboxer._safe_sandbox(Path.cwd() / "coda-update-sandbox-test")
        with self.assertRaises(ValueError):
            sandboxer._safe_sandbox(Path(tempfile.gettempdir()) / "unrelated")

    def test_previous_patch_is_inferred_only_when_safe(self):
        self.assertEqual(sandboxer._previous_patch("1.4.4"), "1.4.3")
        with self.assertRaises(ValueError):
            sandboxer._previous_patch("1.4.0")

    def test_manifest_rejects_unowned_directory(self):
        with tempfile.TemporaryDirectory(
            prefix="coda-update-sandbox-test-", dir=tempfile.gettempdir()
        ) as directory:
            with self.assertRaises((FileNotFoundError, ValueError)):
                sandboxer._manifest(Path(directory))

    def test_status_counts_attempt_directories_not_active_marker(self):
        with tempfile.TemporaryDirectory(
            prefix="coda-update-sandbox-test-", dir=tempfile.gettempdir()
        ) as directory:
            root = Path(directory)
            installation = root / sandboxer.INSTALLATION_NAME
            installation.mkdir()
            (installation / "version.json").write_text(
                '{"version":"1.4.4"}', encoding="utf-8"
            )
            (installation / ".coda-update-test").mkdir()
            (installation / ".coda-update-active.json").write_text(
                "{}", encoding="utf-8"
            )
            (root / sandboxer.MANIFEST_NAME).write_text(
                '{"format":1,"from_version":"1.4.3","target_version":"1.4.4",'
                '"target_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
                '"configuration_copied":false}', encoding="utf-8",
            )
            output = io.StringIO()
            with patch("sys.stdout", output):
                sandboxer.status(root)
            self.assertIn("Generated update attempts: 1", output.getvalue())

    def test_wizard_can_exit_existing_sandbox_without_changes(self):
        with tempfile.TemporaryDirectory(
            prefix="coda-update-sandbox-test-", dir=tempfile.gettempdir()
        ) as directory:
            root = Path(directory)
            (root / sandboxer.MANIFEST_NAME).write_text(
                '{"format":1,"from_version":"1.4.3","target_version":"1.4.4",'
                '"target_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
                '"configuration_copied":false}', encoding="utf-8",
            )
            with patch.object(sandboxer, "status"), patch("builtins.input", return_value="exit"):
                self.assertEqual(sandboxer.wizard(root), 0)
