import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from utils import update_manager as updater


class UpdateManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "main.py").write_text("old code", encoding="utf-8")
        (self.root / ".env").write_text("local settings", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "keep").write_text("environment", encoding="utf-8")
        (self.root / "notes.txt").write_text("personal", encoding="utf-8")

    def download(self, workspace):
        with zipfile.ZipFile(workspace / updater.ZIP_NAME, "w") as archive:
            for name, value in {
                "main.py": "new code",
                "version.json": json.dumps({"version": "1.4.4"}),
                "requirements.txt": "requests",
                ".env": "release default",
                "wakewords.json": "[]",
            }.items():
                archive.writestr(f"coda-main/{name}", value)

    def run_update(self):
        with patch.object(updater, "update_program", side_effect=self.download):
            return updater.main(self.root)

    def assert_local_files(self):
        self.assertEqual((self.root / ".env").read_text(), "local settings")
        self.assertEqual((self.root / ".venv" / "keep").read_text(), "environment")
        self.assertEqual((self.root / "notes.txt").read_text(), "personal")

    def test_success_preserves_local_files_and_unique_backup(self):
        stale = self.root / updater.BACKUP_DIR
        stale.mkdir()
        (stale / "keep").write_text("previous backup")
        self.assertTrue(self.run_update())
        self.assertEqual((self.root / "main.py").read_text(), "new code")
        self.assert_local_files()
        self.assertEqual((stale / "keep").read_text(), "previous backup")
        attempt = next(self.root.glob(".coda-update-*"))
        self.assertEqual((attempt / updater.BACKUP_DIR / "main.py").read_text(), "old code")

    def test_download_failure_never_activates_stale_backup(self):
        stale = self.root / updater.BACKUP_DIR
        stale.mkdir()
        (stale / "main.py").write_text("stale code")
        with patch.object(updater, "update_program", side_effect=OSError("offline")):
            self.assertFalse(updater.main(self.root))
        self.assertEqual((self.root / "main.py").read_text(), "old code")
        self.assertEqual((stale / "main.py").read_text(), "stale code")
        self.assert_local_files()

    def test_repeated_updates_keep_separate_backups(self):
        self.assertTrue(self.run_update())
        first = next(self.root.glob(".coda-update-*"))
        self.assertTrue(self.run_update())
        self.assertEqual(len(list(self.root.glob(".coda-update-*"))), 2)
        self.assertEqual((first / updater.BACKUP_DIR / "main.py").read_text(), "old code")
        self.assert_local_files()

    def test_rollback_failure_retains_original_for_manual_recovery(self):
        original_rename = Path.rename

        def fail_install_and_restore(source, target):
            if source.name == "main.py" and source.parent.name in {
                updater.NEW_VERSION_DIR, updater.BACKUP_DIR
            }:
                raise PermissionError("file locked")
            return original_rename(source, target)

        with patch.object(Path, "rename", fail_install_and_restore):
            self.assertFalse(self.run_update())
        attempt = next(self.root.glob(".coda-update-*"))
        self.assertEqual((attempt / updater.BACKUP_DIR / "main.py").read_text(), "old code")
        self.assertEqual((attempt / updater.NEW_VERSION_DIR / "main.py").read_text(), "new code")
        self.assert_local_files()

    def test_partial_activation_restores_only_this_attempt(self):
        original_rename = Path.rename
        for failure_at in (1, 2, 3, 4, 5, 6, 7):
            with self.subTest(failure_at=failure_at):
                calls = 0

                def fail_once(source, target):
                    nonlocal calls
                    calls += 1
                    if calls == failure_at:
                        raise OSError("simulated move failure")
                    return original_rename(source, target)

                with patch.object(Path, "rename", fail_once):
                    self.assertFalse(self.run_update())
                self.assertEqual((self.root / "main.py").read_text(), "old code")
                self.assertFalse((self.root / "version.json").exists())
                self.assertFalse((self.root / "requirements.txt").exists())
                self.assert_local_files()

    def test_default_target_uses_install_root_not_cwd(self):
        with (
            patch.object(updater, "INSTALL_ROOT", self.root),
            patch.object(updater, "update_program", side_effect=self.download),
            patch.object(Path, "cwd", side_effect=AssertionError("cwd used")),
        ):
            self.assertTrue(updater.main())

    def test_invalid_install_target_is_rejected_before_download(self):
        with patch.object(updater, "update_program") as download:
            self.assertFalse(updater.main(self.root / "missing"))
        download.assert_not_called()

    def test_git_checkout_is_rejected_before_download(self):
        for as_file in (False, True):
            with self.subTest(worktree=as_file):
                marker = self.root / ".git"
                if as_file:
                    marker.write_text("gitdir: elsewhere")
                else:
                    marker.mkdir()
                with patch.object(updater, "update_program") as download:
                    self.assertFalse(updater.main(self.root))
                download.assert_not_called()
                self.assertEqual((self.root / "main.py").read_text(), "old code")
                self.assertFalse(list(self.root.glob(".coda-update-*")))
                self.assert_local_files()
                if as_file:
                    marker.unlink()
                else:
                    marker.rmdir()

    def test_install_nested_in_git_checkout_is_rejected(self):
        (self.root / ".git").mkdir()
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "main.py").write_text("nested code")
        with patch.object(updater, "update_program") as download:
            self.assertFalse(updater.main(nested))
        download.assert_not_called()
        self.assertEqual((nested / "main.py").read_text(), "nested code")

    def test_reserved_archive_entries_cannot_replace_environment(self):
        def download(workspace):
            self.download(workspace)
            with zipfile.ZipFile(workspace / updater.ZIP_NAME, "a") as archive:
                archive.writestr("coda-main/.venv/replacement", "bad")

        with patch.object(updater, "update_program", side_effect=download):
            self.assertFalse(updater.main(self.root))
        self.assert_local_files()
        self.assertEqual((self.root / "main.py").read_text(), "old code")

    def test_path_traversal_archive_is_rejected(self):
        def download(workspace):
            with zipfile.ZipFile(workspace / updater.ZIP_NAME, "w") as archive:
                archive.writestr("../../escaped", "bad")

        with patch.object(updater, "update_program", side_effect=download):
            self.assertFalse(updater.main(self.root))
        self.assertFalse((self.root / "escaped").exists())
        self.assert_local_files()

    def test_archive_symlinks_are_rejected(self):
        def download(workspace):
            entry = zipfile.ZipInfo("coda-main/linked")
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            with zipfile.ZipFile(workspace / updater.ZIP_NAME, "w") as archive:
                archive.writestr(entry, "../../elsewhere")

        with patch.object(updater, "update_program", side_effect=download):
            self.assertFalse(updater.main(self.root))
        self.assert_local_files()

    def test_caller_propagates_update_failure(self):
        import coda_runtime

        with (
            patch("builtins.open", unittest.mock.mock_open(read_data='{"version":"1.0.0"}')),
            patch.object(coda_runtime.requests, "get") as get,
            patch("builtins.input", return_value="y"),
            patch.object(updater, "main", return_value=False),
        ):
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"version": "1.4.4"}
            self.assertFalse(coda_runtime.check_update_available("unused"))


if __name__ == "__main__":
    unittest.main()
