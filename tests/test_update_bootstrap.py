import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from utils import update_bootstrap as bootstrap
from utils import update_manager as updater


class BootstrapTests(unittest.TestCase):
    def test_invalid_environment_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("../environment", ".venv", ".coda-update-x/../environment", "elsewhere/environment"):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    bootstrap.managed_environment(root, relative)

    def test_launch_environment_preserves_cache_settings_and_replaces_activation(self):
        python = Path("candidate/environment/Scripts/python.exe")
        with patch.dict(os.environ, {"HF_HOME": "model-cache", "PYTHONPATH": "stale", "PYTHONHOME": "stale"}):
            env = bootstrap.launch_environment(python)
        self.assertEqual(env["HF_HOME"], "model-cache")
        self.assertEqual(env["VIRTUAL_ENV"], str(python.parent.parent))
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PYTHONHOME", env)

    def test_redirect_keeps_arguments_and_waits_for_child(self):
        root = Path.cwd()
        python = root / "candidate" / "python"
        child = Mock()
        child.wait.return_value = 17
        with patch.object(bootstrap, "selected_python", return_value=python), \
                patch.object(bootstrap.subprocess, "Popen", return_value=child) as popen:
            self.assertEqual(bootstrap.redirect_to_selected_environment(root, ["-m", "--mic", "Yeti Orb"]), 17)
        self.assertEqual(popen.call_args.args[0], [str(python), str(root / "main.py"), "-m", "--mic", "Yeti Orb"])
        child.wait.assert_called_once()

    def test_no_redirect_when_already_using_selected_interpreter(self):
        with patch.object(bootstrap, "selected_python", return_value=Path(sys.executable)), \
                patch.object(bootstrap.subprocess, "Popen") as popen:
            self.assertIsNone(bootstrap.redirect_to_selected_environment(Path.cwd(), []))
        popen.assert_not_called()

    def test_console_interrupt_escalates_only_after_grace_period(self):
        child = Mock()
        child.wait.side_effect = [KeyboardInterrupt(), subprocess.TimeoutExpired("child", 6),
                                  subprocess.TimeoutExpired("child", 6), 1]
        self.assertEqual(bootstrap.wait_for_process(child), 1)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()

    def test_candidate_timeout_and_early_exit_are_explicit(self):
        update = Mock(workspace=Path.cwd())
        with self.assertRaises(TimeoutError):
            updater.wait_for_candidate(update, Mock(), timeout=0)
        with self.assertRaisesRegex(RuntimeError, "exited"):
            updater.wait_for_candidate(update, Mock(poll=Mock(return_value=1)))


class OfflineRestartTests(unittest.TestCase):
    """Real subprocess handoff in disposable installs, without network or CODA hardware."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / ".coda-update-test"
        self.staged = self.workspace / updater.NEW_VERSION_DIR
        (self.staged / "utils").mkdir(parents=True)
        (self.root / "main.py").write_text("# original main", encoding="utf-8")
        (self.root / ".env").write_text("personal configuration", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "keep").write_text("original environment", encoding="utf-8")
        repo = Path(__file__).resolve().parents[1]
        shutil.copy2(repo / "main.py", self.staged / "main.py")
        shutil.copy2(repo / "utils" / "update_bootstrap.py", self.staged / "utils" / "update_bootstrap.py")
        (self.staged / "coda_runtime.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "from utils.update_bootstrap import acknowledge_handoff\n"
            "def main(skip_update_check=False):\n"
            "    acknowledge_handoff(Path(__file__).resolve().parent)\n"
            "    Path('launch.json').write_text(json.dumps({'args': sys.argv[1:], "
            "'python': sys.executable, 'skip_check': skip_update_check}))\n"
            "    return 0\n", encoding="utf-8",
        )
        environment = self.workspace / "environment"
        subprocess.run(
            [sys.executable, "-I", "-m", "venv", "--copies", "--without-pip", str(environment)],
            check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.python = bootstrap.environment_python(environment)
        self.prepared = updater.PreparedUpdate(self.root, self.workspace, self.python, ("-m", "--mic", "Yeti Orb"))

    def test_handoff_then_later_launch_uses_selected_environment(self):
        child = updater.complete_update(self.prepared)
        self.assertIsNotNone(child)
        self.assertEqual(child.wait(timeout=15), 0)
        launch = json.loads((self.root / "launch.json").read_text())
        self.assertEqual(launch["args"], ["-m", "--mic", "Yeti Orb"])
        self.assertTrue(launch["skip_check"])
        self.assertEqual(Path(launch["python"]), self.python)
        result = subprocess.run(
            [sys.executable, str(self.root / "main.py"), "--manual"],
            cwd=self.root, check=False, timeout=20, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        later = json.loads((self.root / "launch.json").read_text())
        self.assertEqual(later["args"], ["--manual"])
        self.assertEqual(Path(later["python"]), self.python)
        self.assertFalse(later["skip_check"])
        self.assertEqual((self.root / ".env").read_text(), "personal configuration")
        self.assertEqual((self.root / ".venv" / "keep").read_text(), "original environment")

    def test_real_failed_import_restores_source(self):
        (self.staged / "coda_runtime.py").write_text("raise RuntimeError('fixture startup failure')\n")
        self.assertIsNone(updater.complete_update(self.prepared))
        self.assertEqual((self.root / "main.py").read_text(), "# original main")
        self.assertFalse((self.root / bootstrap.ACTIVE_ENVIRONMENT).exists())
        self.assertTrue((self.staged / "coda_runtime.py").is_file())

    def test_git_checkout_created_during_staging_is_refused_at_activation(self):
        (self.root / ".git").mkdir()
        self.assertIsNone(updater.complete_update(self.prepared))
        self.assertEqual((self.root / "main.py").read_text(), "# original main")
        self.assertFalse((self.workspace / updater.BACKUP_DIR).exists())


if __name__ == "__main__":
    unittest.main()
