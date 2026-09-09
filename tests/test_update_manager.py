import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from utils import update_manager as updater
from utils.update_releases import Release, stable_version


class UpdateManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "main.py").write_text("old code", encoding="utf-8")
        (self.root / "version.json").write_text('{"version":"1.4.2"}', encoding="utf-8")
        self.release = Release("v1.4.4", stable_version("1.4.4"), "a" * 40)
        discovery = patch.object(updater, "latest_release", side_effect=lambda: self.release)
        discovery.start()
        self.addCleanup(discovery.stop)
        (self.root / ".env").write_text("local settings", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "keep").write_text("environment", encoding="utf-8")
        (self.root / "notes.txt").write_text("personal", encoding="utf-8")
        dependencies = patch.object(updater, "prepare_environment", side_effect=self.environment)
        self.dependencies = dependencies.start()
        self.addCleanup(dependencies.stop)
        restart = patch.object(updater, "restart_candidate", return_value=Mock())
        self.child = restart.start().return_value
        self.child.wait.return_value = 0
        self.addCleanup(restart.stop)
        readiness = patch.object(updater, "wait_for_candidate")
        self.readiness = readiness.start()
        self.addCleanup(readiness.stop)

    def environment(self, workspace, _staged, _python):
        from utils.update_bootstrap import environment_python
        environment = workspace / "environment"
        python = environment_python(environment)
        python.parent.mkdir(parents=True)
        python.write_text("fake interpreter")
        (environment / "pyvenv.cfg").write_text("test environment")
        return python

    def download(self, workspace, release, progress=None):
        with zipfile.ZipFile(workspace / updater.ZIP_NAME, "w") as archive:
            for name, value in {
                "main.py": "new code",
                "version.json": json.dumps({"version": str(release.version)}),
                "requirements.txt": "requests",
                ".env": "release default",
                "wakewords.json": "[]",
                "utils/update_bootstrap.py": "# restart protocol fixture",
            }.items():
                archive.writestr(f"{release.archive_root}/{name}", value)
        if progress is not None:
            progress(1, 1)

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
        attempt = next(p for p in self.root.glob(".coda-update-*") if p.is_dir())
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
        first = next(p for p in self.root.glob(".coda-update-*") if p.is_dir())
        self.release = Release("v1.4.5", stable_version("1.4.5"), "b" * 40)
        self.assertTrue(self.run_update())
        self.assertEqual(len([p for p in self.root.glob(".coda-update-*") if p.is_dir()]), 2)
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
        attempt = next(p for p in self.root.glob(".coda-update-*") if p.is_dir())
        self.assertEqual((attempt / updater.BACKUP_DIR / "main.py").read_text(), "old code")
        self.assertEqual((attempt / updater.NEW_VERSION_DIR / "main.py").read_text(), "new code")
        self.assert_local_files()

    def test_partial_activation_restores_only_this_attempt(self):
        original_rename = Path.rename
        for failure_at in range(1, 10):
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
                self.assertEqual(json.loads((self.root / "version.json").read_text())["version"], "1.4.2")
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
        def download(workspace, release, progress=None):
            self.download(workspace, release, progress)
            with zipfile.ZipFile(workspace / updater.ZIP_NAME, "a") as archive:
                archive.writestr(f"{release.archive_root}/.venv/replacement", "bad")

        with patch.object(updater, "update_program", side_effect=download):
            self.assertFalse(updater.main(self.root))
        self.assert_local_files()
        self.assertEqual((self.root / "main.py").read_text(), "old code")

    def test_path_traversal_archive_is_rejected(self):
        def download(workspace, _release, progress=None):
            with zipfile.ZipFile(workspace / updater.ZIP_NAME, "w") as archive:
                archive.writestr("../../escaped", "bad")

        with patch.object(updater, "update_program", side_effect=download):
            self.assertFalse(updater.main(self.root))
        self.assertFalse((self.root / "escaped").exists())
        self.assert_local_files()

    def test_archive_symlinks_are_rejected(self):
        def download(workspace, release, progress=None):
            entry = zipfile.ZipInfo(f"{release.archive_root}/linked")
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            with zipfile.ZipFile(workspace / updater.ZIP_NAME, "w") as archive:
                archive.writestr(entry, "../../elsewhere")

        with patch.object(updater, "update_program", side_effect=download):
            self.assertFalse(updater.main(self.root))
        self.assert_local_files()

    def test_caller_propagates_update_failure(self):
        import coda_runtime

        with patch.object(updater, "check_for_update", return_value=None):
            self.assertIsNone(coda_runtime.check_update_available())

    def test_dependency_failure_does_not_activate_source(self):
        self.dependencies.side_effect = RuntimeError("installation failed")
        self.assertFalse(self.run_update())
        self.assertEqual((self.root / "main.py").read_text(), "old code")
        self.assert_local_files()
        self.assertFalse((self.root / updater.ACTIVE_ENVIRONMENT).exists())

    def test_prepare_does_not_replace_source_and_retains_arguments(self):
        with patch.object(updater, "update_program", side_effect=self.download):
            prepared = updater.prepare_update(self.root, arguments=["-m", "--mic", "Yeti Orb"])
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.arguments, ("-m", "--mic", "Yeti Orb"))
        self.assertEqual((self.root / "main.py").read_text(), "old code")
        self.assert_local_files()

    def test_restart_spawn_failure_restores_source_and_selection(self):
        marker = self.root / updater.ACTIVE_ENVIRONMENT
        marker.write_text('{"environment":"old selection"}')
        with patch.object(updater, "restart_candidate", side_effect=OSError("cannot start")):
            self.assertFalse(self.run_update())
        self.assertEqual((self.root / "main.py").read_text(), "old code")
        self.assertEqual(marker.read_text(), '{"environment":"old selection"}')
        self.assert_local_files()

    def test_readiness_failure_stops_child_before_rollback(self):
        events = []
        self.readiness.side_effect = TimeoutError("no startup acknowledgement")
        rollback = updater.rollback_from_backup

        def restore(*args):
            events.append("restore")
            return rollback(*args)

        with (
            patch.object(updater, "_stop_failed_child", side_effect=lambda _child: events.append("stop")),
            patch.object(updater, "rollback_from_backup", side_effect=restore),
        ):
            self.assertFalse(self.run_update())
        self.assertEqual(events, ["stop", "restore"])
        self.assertEqual((self.root / "main.py").read_text(), "old code")
        self.assert_local_files()

    def test_child_cannot_be_stopped_does_not_roll_back_underneath_it(self):
        self.readiness.side_effect = TimeoutError("not ready")
        with (
            patch.object(updater, "_stop_failed_child", side_effect=OSError("cannot stop")),
            patch.object(updater, "rollback_from_backup") as rollback,
        ):
            self.assertFalse(self.run_update())
        rollback.assert_not_called()
        attempt = next(p for p in self.root.glob(".coda-update-*") if p.is_dir())
        self.assertEqual((attempt / updater.BACKUP_DIR / "main.py").read_text(), "old code")

    def test_success_persists_new_environment_for_future_launches(self):
        self.assertTrue(self.run_update())
        from utils.update_bootstrap import selected_python
        python = selected_python(self.root)
        self.assertTrue(python.is_file())
        self.assertEqual(python.parent.parent.name, "environment")
        self.child.wait.assert_called_once()

    def test_selection_commit_failure_restores_previous_source(self):
        original_replace = Path.replace

        def fail_selection(source, target):
            if source.name == updater.ACTIVE_ENVIRONMENT:
                raise PermissionError("selection locked")
            return original_replace(source, target)

        with patch.object(Path, "replace", fail_selection):
            self.assertFalse(self.run_update())
        self.assertFalse((self.root / updater.ACTIVE_ENVIRONMENT).exists())
        self.assertEqual((self.root / "main.py").read_text(), "old code")
        self.assert_local_files()

    def test_failure_after_selection_commit_restores_previous_selection(self):
        marker = self.root / updater.ACTIVE_ENVIRONMENT
        marker.write_text('{"environment":"previous environment"}')
        original_open = Path.open

        def fail_proceed(path, *args, **kwargs):
            if path.name == "restart-proceed":
                raise PermissionError("handoff write failed")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", fail_proceed):
            self.assertFalse(self.run_update())
        self.assertEqual(marker.read_text(), '{"environment":"previous environment"}')
        self.assertEqual((self.root / "main.py").read_text(), "old code")
        self.assert_local_files()

    def test_equal_or_older_release_is_not_prompted_or_prepared(self):
        for version in ("1.4.2", "1.4.0"):
            self.release = Release(f"v{version}", stable_version(version), "a" * 40)
            with self.subTest(version=version), patch("builtins.input") as prompt:
                self.assertIsNone(updater.check_for_update(self.root))
                self.assertIsNone(updater.prepare_update(self.root))
            prompt.assert_not_called()
            self.dependencies.assert_not_called()
        self.assertFalse(list(self.root.glob(".coda-update-*")))

    def test_missing_local_version_never_creates_default_or_discovers_release(self):
        (self.root / "version.json").unlink()
        with patch.object(updater, "latest_release") as discover:
            self.assertIsNone(updater.check_for_update(self.root))
        discover.assert_not_called()
        self.assertFalse((self.root / "version.json").exists())
        self.assert_local_files()

    def test_git_checkout_is_not_prompted_or_checked_remotely(self):
        (self.root / ".git").mkdir()
        with patch.object(updater, "latest_release") as discover, patch("builtins.input") as prompt:
            self.assertIsNone(updater.check_for_update(self.root))
        discover.assert_not_called()
        prompt.assert_not_called()

    def test_confirmed_release_is_carried_to_preparation_without_rediscovery(self):
        confirmed = self.release

        def confirm(_prompt):
            self.release = Release("v1.4.5", stable_version("1.4.5"), "b" * 40)
            return "y"

        with patch("builtins.input", side_effect=confirm), patch.object(updater, "prepare_update") as prepare:
            updater.check_for_update(self.root)
        prepare.assert_called_once_with(self.root, release=confirmed)

    def test_archive_root_must_match_confirmed_commit(self):
        wrong = Release(self.release.tag, self.release.version, "b" * 40)
        with patch.object(
            updater, "update_program",
            side_effect=lambda workspace, _release, progress=None: self.download(workspace, wrong, progress),
        ):
            self.assertIsNone(updater.prepare_update(self.root))
        self.dependencies.assert_not_called()
        self.assertEqual((self.root / "main.py").read_text(), "old code")

    def test_version_mismatch_is_rejected_before_dependency_installation(self):
        def download(workspace, release, progress=None):
            with zipfile.ZipFile(workspace / updater.ZIP_NAME, "w") as archive:
                for name, value in {"main.py": "new code", "requirements.txt": "requests==1.0.0",
                                    "version.json": '{"version":"1.4.3"}'}.items():
                    archive.writestr(f"{release.archive_root}/{name}", value)

        with patch.object(updater, "update_program", side_effect=download):
            self.assertIsNone(updater.prepare_update(self.root))
        self.dependencies.assert_not_called()
        self.assertEqual((self.root / "main.py").read_text(), "old code")
        self.assert_local_files()

    def test_expanded_archive_limits_are_checked_before_extraction(self):
        info = Mock(file_size=513 * 1024 * 1024)
        archive = Mock()
        archive.infolist.return_value = [info]
        with patch.object(updater.zipfile, "ZipFile") as zip_file, patch.object(updater, "update_program"):
            zip_file.return_value.__enter__.return_value = archive
            self.assertIsNone(updater.prepare_update(self.root, release=self.release))
        archive.extractall.assert_not_called()
        self.dependencies.assert_not_called()


if __name__ == "__main__":
    unittest.main()
