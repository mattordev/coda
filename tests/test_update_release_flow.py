"""Offline release-to-restart coverage; never exercise the real installation."""

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from utils import update_manager as updater
from utils import update_releases as releases


def wheel_fixture():
    """Small real wheel proves changed dependencies are usable at child startup."""
    output = io.BytesIO()
    metadata = "coda_upgrade_probe-1.0.0.dist-info"
    with zipfile.ZipFile(output, "w") as wheel:
        entries = {
            "coda_upgrade_probe.py": "VALUE = 'new dependency'\n",
            f"{metadata}/METADATA": "Metadata-Version: 2.1\nName: coda-upgrade-probe\nVersion: 1.0.0\n",
            f"{metadata}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        for name, content in entries.items():
            wheel.writestr(name, content)
        wheel.writestr(f"{metadata}/RECORD", "".join(f"{name},,\n" for name in entries) + f"{metadata}/RECORD,,\n")
    return output.getvalue()


class OfflineReleaseFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "main.py").write_text("# old main\n")
        (self.root / "version.json").write_text('{"version":"1.4.4"}')
        (self.root / ".env").write_text("retained configuration")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "keep").write_text("old environment")
        (self.root / updater.BACKUP_DIR).mkdir()
        (self.root / updater.BACKUP_DIR / "keep").write_text("older backup")
        self.release = releases.Release("v1.4.5", releases.stable_version("1.4.5"), "1" * 40)
        self.urls = []

    def archive(self, fail_startup=False):
        repo = Path(__file__).resolve().parents[1]
        output = io.BytesIO()
        runtime = (
            "import json, sys\nfrom pathlib import Path\n"
            "from coda_upgrade_probe import VALUE\n"
            "from utils.update_bootstrap import acknowledge_handoff\n"
            "def main(skip_update_check=False):\n"
            "    acknowledge_handoff(Path(__file__).resolve().parent)\n"
            "    Path('launch.json').write_text(json.dumps({'dependency': VALUE, 'args': sys.argv[1:]}))\n"
            "    return 0\n"
        )
        if fail_startup:
            runtime += "raise RuntimeError('fixture startup failure')\n"
        with zipfile.ZipFile(output, "w") as archive:
            entries = {
                "main.py": (repo / "main.py").read_bytes(),
                "utils/update_bootstrap.py": (repo / "utils" / "update_bootstrap.py").read_bytes(),
                "coda_runtime.py": runtime,
                "version.json": '{"version":"1.4.5"}',
                # Pip runs inside the attempt directory, not the active install.
                "requirements.txt": "--no-index\n--find-links coda-version-new/wheelhouse\ncoda-upgrade-probe==1.0.0\n",
                "wheelhouse/coda_upgrade_probe-1.0.0-py3-none-any.whl": wheel_fixture(),
            }
            for name, content in entries.items():
                archive.writestr(f"{self.release.archive_root}/{name}", content)
        return output.getvalue()

    def fake_http(self, archive):
        def get(url, **_kwargs):
            self.urls.append(url)
            if url == f"{releases.API_ROOT}/releases/latest":
                body = json.dumps({"tag_name": "v1.4.5", "draft": False, "prerelease": False,
                                   "published_at": "2026-09-07T00:00:00Z", "target_commitish": "main"}).encode()
            elif url == f"{releases.API_ROOT}/git/ref/tags/v1.4.5":
                body = json.dumps({"ref": "refs/tags/v1.4.5", "object": {"type": "commit", "sha": self.release.commit}}).encode()
            elif url == self.release.archive_url:
                body = archive
            else:
                raise AssertionError(f"Unexpected network target: {url}")
            response = Mock(status_code=200, headers={"Content-Length": str(len(body))})
            response.iter_content.return_value = [body]
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            return response
        return get

    def prepared(self, fail_startup=False):
        with (
            patch.object(releases.requests, "get", side_effect=self.fake_http(self.archive(fail_startup))),
            patch("builtins.input", return_value="y"),
            patch.object(sys, "argv", ["main.py", "-m", "--mic", "Fixture microphone"]),
        ):
            result = updater.check_for_update(self.root)
        self.assertIsNotNone(result)
        self.assertEqual(len(self.urls), 3)  # No rediscovery after confirmation.
        self.assertEqual((self.root / "main.py").read_text(), "# old main\n")
        self.assertEqual(json.loads((result.workspace / "release.json").read_text())["commit"], self.release.commit)
        return result

    def assert_preserved(self):
        self.assertEqual((self.root / ".env").read_text(), "retained configuration")
        self.assertEqual((self.root / ".venv" / "keep").read_text(), "old environment")
        self.assertEqual((self.root / updater.BACKUP_DIR / "keep").read_text(), "older backup")

    def test_release_selection_install_and_restart_with_changed_dependency(self):
        prepared = self.prepared()
        child = updater.complete_update(prepared)
        self.assertIsNotNone(child)
        self.assertEqual(child.wait(timeout=20), 0)
        launch = json.loads((self.root / "launch.json").read_text())
        self.assertEqual(launch, {"dependency": "new dependency", "args": ["-m", "--mic", "Fixture microphone"]})
        self.assertEqual(releases.read_installed_version(self.root), self.release.version)
        self.assert_preserved()
        check = subprocess.run([str(prepared.python), "-I", "-m", "pip", "check"],
                               capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_full_preparation_then_failed_restart_restores_old_release(self):
        prepared = self.prepared(fail_startup=True)
        self.assertIsNone(updater.complete_update(prepared))
        self.assertEqual((self.root / "main.py").read_text(), "# old main\n")
        self.assertEqual(str(releases.read_installed_version(self.root)), "1.4.4")
        self.assertFalse((self.root / updater.ACTIVE_ENVIRONMENT).exists())
        self.assertTrue(prepared.python.is_file())
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
