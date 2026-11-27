"""Optional browser evaluation must remain inert without explicit opt-in."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import browser_candidate_probe as probe


class BrowserCandidateProbeTests(unittest.TestCase):
    def test_default_does_not_import_sdk_or_start_browser(self):
        before = {name for name in sys.modules if name.startswith('playwright')}
        output = io.StringIO()
        with patch.object(probe, 'evaluate') as evaluate, patch('subprocess.Popen') as launch, redirect_stdout(output):
            probe.main([])
        self.assertFalse(json.loads(output.getvalue())['enabled'])
        self.assertEqual({name for name in sys.modules if name.startswith('playwright')}, before)
        evaluate.assert_not_called()
        launch.assert_not_called()

    def test_missing_explicit_dependency_directory_does_not_attempt_install_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / 'missing')
            with patch.object(probe, 'evaluate') as evaluate, patch('subprocess.Popen') as launch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    probe.main(['--enabled', '--packages-dir', missing])
                self.assertEqual(caught.exception.code, 2)
            evaluate.assert_not_called()
            launch.assert_not_called()

    def test_existing_evidence_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text('Existing evidence', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                probe.main(['--output', str(path)])
            self.assertEqual(path.read_text(encoding='utf-8'), 'Existing evidence')


if __name__ == '__main__':
    unittest.main()
