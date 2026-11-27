from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace
import unittest

from runtime.process_executor import execute_approved_action
from runtime.owned_process import start_owned_process


@unittest.skipUnless(sys.platform in ("win32", "linux"), "Windows and Linux process ownership")
class OwnedProcessTests(unittest.TestCase):
    def test_timeout_closes_pipes_held_by_a_descendant(self):
        executable = str(Path(sys.executable).resolve())
        action = SimpleNamespace(
            executable=executable,
            argv=("-I", "-c",
                  "import subprocess, sys, time; "
                  "subprocess.Popen([sys.argv[1], '-I', '-c', 'import time; time.sleep(20)']); "
                  "print('child started', flush=True); time.sleep(20)", executable),
            cwd=str(Path.cwd()), environment=(), timeout_seconds=2, max_output_bytes=256,
        )
        result = execute_approved_action(action, (), Event())
        self.assertEqual(result.state, "timed_out")
        self.assertEqual(result.stdout.text.strip(), "child started")
        self.assertEqual(result.stderr.text, "")

    def test_preserves_program_output_and_exit_status(self):
        action = SimpleNamespace(
            executable=str(Path(sys.executable).resolve()),
            argv=("-I", "-c", "import sys; print('probe'); sys.exit(7)"),
            cwd=str(Path.cwd()), environment=(),
        )
        with start_owned_process(action) as process:
            self.assertEqual(process.wait(timeout=10), 7)
            self.assertEqual(process.stdout.read().strip(), b"probe")
