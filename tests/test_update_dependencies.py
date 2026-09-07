import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import update_dependencies as dependencies


class UpdateDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.staged = self.workspace / "staged"
        self.staged.mkdir()
        (self.staged / "requirements.txt").write_text("example==1.2.3\n", encoding="utf-8")
        self.calls = []

    def fake_run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if "venv" in command:
            interpreter = dependencies.environment_python(Path(command[-1]))
            interpreter.parent.mkdir(parents=True)
            interpreter.write_bytes(b"stub interpreter")
        return subprocess.CompletedProcess(command, 0)

    def prepare(self):
        return dependencies.prepare_environment(self.workspace, self.staged, sys.executable)

    def test_creates_installs_and_checks_in_order_without_mutating_running_env(self):
        with patch.object(dependencies.subprocess, "run", side_effect=self.fake_run), \
                patch.object(dependencies.shutil, "which", return_value="ffplay"), \
                patch.dict(os.environ, {"HF_HOME": "preserved-cache", "PYTHONPATH": "old-path",
                                        "PYTHONHOME": "old-home", "VIRTUAL_ENV": "running-env",
                                        "PIP_CONFIG_FILE": "global-prefix-config"}):
            interpreter = self.prepare()
        self.assertEqual(interpreter, dependencies.environment_python(self.workspace / "environment"))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[0][0], [str(Path(sys.executable).resolve()), "-I", "-m",
                                          "venv", "--copies", str(self.workspace / "environment")])
        self.assertEqual(self.calls[1][0], [str(interpreter), "-I", "-m", "pip", "--isolated",
                                          "--require-virtualenv", "install", "--disable-pip-version-check",
                                          "--no-input", "--no-user", "-r", str(self.staged / "requirements.txt")])
        self.assertEqual(self.calls[2][0], [str(interpreter), "-I", "-m", "pip", "--isolated",
                                          "--require-virtualenv", "check"])
        for index, (_, options) in enumerate(self.calls):
            self.assertTrue(options["check"])
            self.assertEqual(options["timeout"], [120, 1200, 120][index])
            self.assertEqual(options["cwd"], str(self.workspace))
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options["stderr"], subprocess.STDOUT)
            self.assertEqual(options["env"]["HF_HOME"], "preserved-cache")
            self.assertEqual(options["env"]["PIP_CONFIG_FILE"], os.devnull)
            if index:
                self.assertTrue(options["env"]["PATH"].startswith(str(interpreter.parent) + os.pathsep))
            for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
                self.assertNotIn(name, options["env"])
        self.assertIn("verification", (self.workspace / dependencies.LOG_NAME).read_text())

    def test_windows_and_posix_interpreter_layouts(self):
        for platform, expected in (("win32", "Scripts/python.exe"), ("linux", "bin/python"),
                                   ("darwin", "bin/python")):
            with self.subTest(platform=platform), patch.object(
                dependencies.sys, "platform", platform
            ):
                self.assertEqual(dependencies.environment_python(self.workspace), self.workspace / expected)

    def test_missing_ffplay_warns_but_does_not_fail_preparation(self):
        with patch.object(dependencies.subprocess, "run", side_effect=self.fake_run), \
                patch.object(dependencies.shutil, "which", return_value=None), \
                patch("builtins.print") as output:
            self.assertTrue(self.prepare().is_file())
        output.assert_called_once()
        self.assertIn("pyttsx3", output.call_args.args[0])
        self.assertIn("Install FFmpeg", output.call_args.args[0])

    def test_each_subprocess_failure_stops_later_steps_and_retains_log(self):
        for failure_index in range(3):
            with self.subTest(failure_index=failure_index), tempfile.TemporaryDirectory() as temp:
                workspace = Path(temp)
                staged = workspace / "staged"
                staged.mkdir()
                (staged / "requirements.txt").write_text("example==1.2.3")
                self.calls = []

                def fail_step(command, **kwargs):
                    if len(self.calls) == failure_index:
                        self.calls.append((command, kwargs))
                        raise subprocess.CalledProcessError(1, command, output="secret index password")
                    return self.fake_run(command, **kwargs)

                with patch.object(dependencies.subprocess, "run", side_effect=fail_step):
                    with self.assertRaises(dependencies.DependencyPreparationError) as error:
                        dependencies.prepare_environment(workspace, staged, sys.executable)
                self.assertEqual(len(self.calls), failure_index + 1)
                self.assertNotIn("secret", str(error.exception))
                self.assertIn("current environment is unchanged", str(error.exception))
                self.assertTrue((workspace / dependencies.LOG_NAME).is_file())
                self.assertTrue((workspace / dependencies.ENVIRONMENT_DIR).is_dir())

    def test_timeout_produces_actionable_error_without_continuing(self):
        with patch.object(dependencies.subprocess, "run", side_effect=subprocess.TimeoutExpired("venv", 120)):
            with self.assertRaisesRegex(dependencies.DependencyPreparationError, "timed out"):
                self.prepare()

    def test_existing_environment_is_not_reused_or_modified(self):
        environment = self.workspace / dependencies.ENVIRONMENT_DIR
        environment.mkdir()
        marker = environment / "keep"
        marker.write_text("existing environment")
        with patch.object(dependencies.subprocess, "run") as runner:
            with self.assertRaises(FileExistsError):
                self.prepare()
        runner.assert_not_called()
        self.assertEqual(marker.read_text(), "existing environment")

    def test_missing_generated_interpreter_fails_before_pip(self):
        with patch.object(dependencies.subprocess, "run") as runner:
            with self.assertRaisesRegex(dependencies.DependencyPreparationError, "no Python interpreter"):
                self.prepare()
        runner.assert_called_once()

    def test_missing_requirements_fails_before_environment_creation(self):
        (self.staged / "requirements.txt").unlink()
        with patch.object(dependencies.subprocess, "run") as runner:
            with self.assertRaises(FileNotFoundError):
                self.prepare()
        runner.assert_not_called()
        self.assertFalse((self.workspace / dependencies.ENVIRONMENT_DIR).exists())

    def test_source_outside_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(dependencies.subprocess, "run") as runner:
            with self.assertRaisesRegex(ValueError, "within the update workspace"):
                dependencies.prepare_environment(self.workspace, Path(temp), sys.executable)
        runner.assert_not_called()

    @unittest.skipUnless(os.environ.get("CODA_RUN_UPDATE_ENV_SMOKE") == "1", "opt-in offline venv smoke")
    def test_real_offline_environment_with_empty_requirements(self):
        (self.staged / "requirements.txt").write_text("--no-index\n", encoding="utf-8")
        with patch.object(dependencies.shutil, "which", return_value="ffplay"):
            interpreter = self.prepare()
        self.assertTrue(interpreter.is_file())
        result = subprocess.run(
            [str(interpreter), "-I", "-c", "import sys; print(sys.prefix)"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(Path(result.stdout.strip()), self.workspace / dependencies.ENVIRONMENT_DIR)


if __name__ == "__main__":
    unittest.main()
