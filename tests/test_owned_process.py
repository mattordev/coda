from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from runtime.owned_process import start_owned_process


@unittest.skipUnless(sys.platform in ("win32", "linux"), "Windows and Linux process ownership")
class OwnedProcessTests(unittest.TestCase):
    def test_preserves_program_output_and_exit_status(self):
        action = SimpleNamespace(
            executable=str(Path(sys.executable).resolve()),
            argv=("-I", "-c", "import sys; print('probe'); sys.exit(7)"),
            cwd=str(Path.cwd()), environment=(),
        )
        with start_owned_process(action) as process:
            self.assertEqual(process.wait(timeout=10), 7)
            self.assertEqual(process.stdout.read().strip(), b"probe")
