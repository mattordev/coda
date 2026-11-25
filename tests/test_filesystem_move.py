"""Moves require two authorities and never silently become copy-and-delete."""

import errno
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

SERVER = Path(__file__).resolve().parents[1] / 'integrations/coda-filesystem/server.py'
SPEC = importlib.util.spec_from_file_location('move_filesystem', SERVER)
filesystem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filesystem)


class FilesystemMoveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.input = self.base / 'input'
        self.output = self.base / 'output'
        self.input.mkdir()
        self.output.mkdir()
        self.source = self.input / 'notes.txt'
        self.source.write_bytes(b'original\r\n\0\xff')
        self.target = self.output / 'renamed.txt'
        self.config = self.base / 'permissions.json'
        self.configure()
        guard = patch.object(filesystem, 'CONFIG_PATH', self.config)
        guard.start()
        self.addCleanup(guard.stop)

    def configure(self, *, destructive=None, write=None):
        self.config.write_text(json.dumps({
            'read': {'allowed_roots': [], 'blocked_paths': ['**/.env']},
            'write': write if write is not None else {'enabled': True, 'allowed_roots': [str(self.output)]},
            'destructive': destructive if destructive is not None else {'enabled': True, 'allowed_roots': [str(self.input)]},
        }), encoding='utf-8')

    def move(self, source=None, target=None, **limits):
        return filesystem.move_path(str(source or self.source), str(target or self.target), **limits)

    def error(self, result, code):
        self.assertTrue(result.is_error, result)
        self.assertEqual(result.structured_content['error']['code'], code, result)

    def folder(self):
        directory = self.input / 'folder'
        directory.mkdir()
        (directory / 'empty').mkdir()
        (directory / 'notes').write_bytes(b'notes\r\n')
        return directory

    def test_move_and_same_parent_rename_preserve_identity_and_binary_bytes_without_read_grant(self):
        original = self.source.stat()
        result = self.move()
        self.assertFalse(result.is_error, result)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.target.read_bytes(), b'original\r\n\0\xff')
        self.assertEqual((self.target.stat().st_dev, self.target.stat().st_ino), (original.st_dev, original.st_ino))
        self.assertFalse((self.input / '.coda-lock').exists())
        self.assertFalse((self.output / '.coda-lock').exists())
        self.configure(write={'enabled': True, 'allowed_roots': [str(self.input)]})
        original_name = self.input / 'old.txt'
        original_name.write_bytes(b'same parent')
        renamed = self.input / 'new.txt'
        result = self.move(original_name, renamed)
        self.assertFalse(result.is_error, result)
        self.assertEqual(renamed.read_bytes(), b'same parent')
        self.assertFalse((self.input / '.coda-lock').exists())

    def test_complete_tree_move_preserves_empty_directories_and_cleans_relocated_locks(self):
        folder = self.folder()
        result = self.move(folder)
        self.assertFalse(result.is_error, result)
        self.assertFalse(folder.exists())
        self.assertTrue((self.target / 'empty').is_dir())
        self.assertEqual((self.target / 'notes').read_bytes(), b'notes\r\n')
        self.assertEqual(result.structured_content['entries_moved'], 3)
        self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_source_destructive_and_destination_write_grants_are_independent(self):
        self.configure(destructive={})
        self.error(self.move(), 'destructive_disabled')
        self.configure(write={})
        self.error(self.move(), 'write_disabled')
        self.configure(destructive={'enabled': True, 'allowed_roots': [str(self.output)]})
        self.error(self.move(), 'outside_allowed_roots')
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_existing_destinations_and_missing_parent_never_remove_source(self):
        self.target.write_bytes(b'existing')
        self.error(self.move(), 'already_exists')
        self.assertEqual(self.target.read_bytes(), b'existing')
        self.target.unlink()
        self.target.mkdir()
        self.error(self.move(), 'already_exists')
        self.error(self.move(target=self.output / 'missing' / 'new'), 'not_found')
        self.assertTrue(self.source.exists())

    def test_root_and_self_descendant_moves_are_rejected(self):
        self.error(self.move(self.input), 'protected_path')
        folder = self.folder()
        self.configure(write={'enabled': True, 'allowed_roots': [str(self.input)]})
        self.error(self.move(folder, folder / 'nested'), 'invalid_arguments')
        self.assertTrue((folder / 'notes').exists())
        self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_blocked_descendants_and_destination_blocks_fail_before_locks(self):
        folder = self.folder()
        (folder / '.env').write_text('secret', encoding='utf-8')
        self.error(self.move(folder), 'blocked_path')
        (folder / '.env').unlink()
        self.configure(write={'enabled': True, 'allowed_roots': [str(self.output)], 'blocked_paths': ['**/notes']})
        self.error(self.move(folder), 'blocked_path')
        self.assertTrue((folder / 'notes').exists())
        self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_symbolic_links_and_hardlink_aliases_are_refused(self):
        alias = self.input / 'alias'
        try:
            alias.symlink_to(self.source)
        except OSError:
            self.skipTest('Native symbolic links unavailable')
        self.error(self.move(alias), 'unsafe_path')
        alias.unlink()
        os.link(self.source, alias)
        self.error(self.move(), 'unsafe_path')
        self.assertTrue(self.source.exists())
        self.assertTrue(alias.exists())

    @unittest.skipUnless(os.name == 'nt', 'Native Windows junction semantics')
    def test_native_junction_source_and_destination_ancestors_are_refused(self):
        actual = self.input / 'actual'
        actual.mkdir()
        (actual / 'notes').write_bytes(b'original')
        alias = self.input / 'alias'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(actual)], capture_output=True, text=True)
        if made.returncode:
            self.skipTest('Junction creation unavailable')
        try:
            self.error(self.move(alias), 'unsafe_path')
            self.configure(write={'enabled': True, 'allowed_roots': [str(self.input)]})
            self.error(self.move(target=alias / 'new'), 'blocked_path')
            self.assertTrue((actual / 'notes').exists())
            self.assertTrue(self.source.exists())
            self.assertFalse((actual / 'new').exists())
        finally:
            alias.rmdir()

    def test_move_lock_excludes_concurrent_text_mutation(self):
        import hashlib
        self.config.write_text(json.dumps({
            'read': {'allowed_roots': [str(self.input)], 'blocked_paths': []},
            'write': {'enabled': True, 'allowed_roots': [str(self.input), str(self.output)]},
            'destructive': {'enabled': True, 'allowed_roots': [str(self.input)]},
        }), encoding='utf-8')
        rename = filesystem._rename_no_replace
        observed = []
        def concurrent_edit(source, target):
            observed.append(filesystem.append_file(str(self.source), 'unapproved concurrent text',
                hashlib.sha256(self.source.read_bytes()).hexdigest()))
            return rename(source, target)
        with patch.object(filesystem, '_rename_no_replace', side_effect=concurrent_edit):
            result = self.move()
        self.assertFalse(result.is_error, result)
        self.error(observed[0], 'mutation_in_progress')
        self.assertEqual(self.target.read_bytes(), b'original\r\n\0\xff')

    def test_entry_depth_and_argument_limits_leave_tree_intact(self):
        folder = self.folder()
        for arguments in ({'max_entries': 2}, {'max_depth': 0}, {'max_entries': True}, {'max_depth': 17}):
            result = self.move(folder, **arguments)
            self.assertTrue(result.is_error, result)
            self.assertTrue((folder / 'notes').exists())
            self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_new_descendant_after_preflight_is_not_silently_moved(self):
        folder = self.folder()
        original = filesystem._lock_destructive_scope
        def add_file(*args, **kwargs):
            original(*args, **kwargs)
            (folder / 'new.txt').write_bytes(b'not preflighted')
        with patch.object(filesystem, '_lock_destructive_scope', side_effect=add_file):
            self.error(self.move(folder), 'content_changed')
        self.assertTrue((folder / 'new.txt').exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_policy_revocation_before_rename_leaves_both_locations_unchanged(self):
        original = filesystem._lock_destructive_scope
        def revoke(*args, **kwargs):
            original(*args, **kwargs)
            self.configure(write={})
        with patch.object(filesystem, '_lock_destructive_scope', side_effect=revoke):
            result = self.move()
        self.assertTrue(result.is_error, result)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_competing_destination_is_never_overwritten(self):
        original = filesystem._rename_no_replace
        def compete(source, target):
            target.write_bytes(b'competing creator')
            return original(source, target)
        with patch.object(filesystem, '_rename_no_replace', side_effect=compete):
            result = self.move()
        self.error(result, 'move_incomplete')
        self.assertEqual(result.structured_content['cause_code'], 'already_exists')
        self.assertTrue(self.source.exists())
        self.assertEqual(self.target.read_bytes(), b'competing creator')
        self.assertTrue((self.input / '.coda-lock').exists())

    def test_cross_device_rename_error_has_no_copy_delete_fallback(self):
        with patch.object(filesystem, '_rename_no_replace', side_effect=OSError(errno.EXDEV, 'Cross device')):
            result = self.move()
        self.error(result, 'move_incomplete')
        self.assertEqual(result.structured_content['cause_code'], 'cross_device')
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())
        self.assertTrue((self.input / '.coda-lock').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Native Linux separate-volume fixture')
    def test_real_cross_filesystem_move_is_refused_before_creating_locks(self):
        other = Path('/dev/shm')
        if not other.is_dir() or other.stat().st_dev == self.source.stat().st_dev:
            self.skipTest('No writable second filesystem available')
        try:
            temporary = tempfile.TemporaryDirectory(dir=other)
        except OSError:
            self.skipTest('Second filesystem unavailable for disposable fixture')
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name)
        self.configure(write={'enabled': True, 'allowed_roots': [str(output)]})
        self.error(self.move(target=output / 'new.txt'), 'cross_device')
        self.assertTrue(self.source.exists())
        self.assertEqual(list(output.iterdir()), [])
        self.assertFalse((self.input / '.coda-lock').exists())

    def test_rename_raising_after_effect_reports_uncertainty_and_preserves_evidence(self):
        folder = self.folder()
        original = filesystem._rename_no_replace
        def uncertain(source, target):
            original(source, target)
            raise OSError('Remote response lost after rename')
        with patch.object(filesystem, '_rename_no_replace', side_effect=uncertain):
            result = self.move(folder)
        self.error(result, 'move_incomplete')
        self.assertFalse(folder.exists())
        self.assertEqual((self.target / 'notes').read_bytes(), b'notes\r\n')
        self.assertTrue((self.target / '.coda-lock').exists())
        self.assertEqual(result.structured_content['possible_relocated_root'], str(self.target))

    def host(self, mode='persistent'):
        from ai.mcp.config import discover_definitions
        from ai.mcp.runtime import LocalMCPManager
        import utils.on_command as runtime
        import utils.llm_service as history
        for name in ('_mcp_catalog', '_pending_confirmation', '_intent_registry', '_intent_router'):
            guard = patch.object(runtime, name, None)
            guard.start()
            self.addCleanup(guard.stop)
        history._reset_conversation()
        self.addCleanup(history._reset_conversation)
        routing = patch.object(runtime, 'create_router', side_effect=lambda registry: runtime.IntentRouter(
            registry, strategies=(runtime.ExactMatchStrategy(), runtime.CommandPrefixStrategy())))
        routing.start()
        self.addCleanup(routing.stop)
        fallback = patch.object(runtime, '_llm_fallback_enabled', return_value=False)
        fallback.start()
        self.addCleanup(fallback.stop)
        definitions = self.base / 'mcp_servers'
        definitions.mkdir()
        (definitions / 'files.json').write_text(json.dumps({'id': 'files', 'bundled_server': 'filesystem',
            'trusted': True, 'enabled': True, 'settings': {'config_file': str(self.config)},
            'session_mode': mode, 'allowed_tools': ['move_path'], 'confirm_tools': []}), encoding='utf-8')
        found = discover_definitions(definitions)
        self.assertEqual(found.errors, {})
        runtime.configure_intent_router({}, mcp_manager=LocalMCPManager(found.servers))
        self.addCleanup(runtime.shutdown_tools)
        return runtime

    def test_real_host_one_use_confirmation_binds_both_paths_and_cancel_preserves_source(self):
        runtime = self.host()
        command = 'mcp.files.move_path ' + json.dumps({'source': str(self.source), 'destination': str(self.target)})
        pending = runtime.run(command, {})
        self.assertIn(str(self.source), pending.response_text)
        self.assertIn(str(self.target), pending.response_text)
        self.assertTrue(self.source.exists())
        runtime.run('cancel', {})
        self.assertTrue(self.source.exists())
        runtime.run(command, {})
        result = runtime.run('confirm', {})
        self.assertTrue(result.tool_result.success, result)
        self.assertIn('Moved the file', result.response_text)
        self.assertFalse(self.source.exists())
        self.assertTrue(self.target.exists())
        self.source.write_bytes(b'new data')
        runtime.run('confirm', {})
        self.assertEqual(self.source.read_bytes(), b'new data')

    def test_real_operation_mode_rechecks_revoked_policy_on_confirmation(self):
        runtime = self.host(mode='operation')
        runtime.run('mcp.files.move_path ' + json.dumps({'source': str(self.source), 'destination': str(self.target)}), {})
        self.configure(destructive={})
        result = runtime.run('confirm', {})
        self.assertFalse(result.tool_result.success)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())

    def interrupted(self, action):
        import psutil
        from ai.intents import IntentRequest
        from ai.mcp.config import LocalServerDefinition
        from ai.mcp.runtime import LocalMCPManager
        folder = self.folder()
        marker = self.base / 'renamed'
        wrapper = self.base / 'paused-move.py'
        wrapper.write_text('''
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('paused_move_fixture', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.CONFIG_PATH = Path(sys.argv[2])
original = module._rename_no_replace
def paused(source, destination):
    original(source, destination)
    marker = Path(sys.argv[3])
    pending = marker.with_suffix('.pending')
    pending.write_text(str(os.getpid()), encoding='utf-8')
    pending.replace(marker)
    time.sleep(60)
module._rename_no_replace = paused
module.server.run(transport='stdio')
''', encoding='utf-8')
        definition = LocalServerDefinition('active', sys.executable,
            args=(str(wrapper), str(SERVER), str(self.config), str(marker)), trusted=True,
            allowed_tools=frozenset({'move_path'}), session_mode='persistent',
            startup_timeout_seconds=15, timeout_seconds=10 if action == 'cancel' else 2)
        manager = LocalMCPManager([definition])
        cancelled = Event()
        worker = None
        results = []
        try:
            found = manager.discover('active')
            self.assertTrue(found.success, found.error)
            intent = next(intent for intent in found.data if intent.name == 'mcp.active.move_path')
            request = IntentRequest(intent, 'Move approved folder', 1,
                arguments={'source': str(folder), 'destination': str(self.target)})
            worker = Thread(target=lambda: results.append(manager.executor(intent.name).execute(
                request, cancel_event=cancelled, confirmed=True)), daemon=True)
            worker.start()
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                cancelled.wait(0.01)
            self.assertTrue(marker.exists(), f'The real rename must finish before interruption: {results!r}')
            pid = int(marker.read_text(encoding='utf-8'))
            if action == 'cancel':
                cancelled.set()
            worker.join(12)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results[0].error.code, 'cancelled' if action == 'cancel' else 'timeout')
            self.assertTrue(manager.close()['active'].success)
            deadline = time.monotonic() + 5
            while psutil.pid_exists(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(psutil.pid_exists(pid))
            self.assertFalse(folder.exists())
            self.assertEqual((self.target / 'notes').read_bytes(), b'notes\r\n')
            self.assertTrue((self.target / '.coda-lock').exists())
            self.assertTrue((self.input / '.coda-lock').exists())
            self.assertTrue((self.output / '.coda-lock').exists())
            # A new request must never infer success or replay the rename.
            self.error(self.move(folder), 'not_found')
            self.assertEqual((self.target / 'notes').read_bytes(), b'notes\r\n')
        finally:
            cancelled.set()
            manager.close()
            if worker is not None:
                worker.join(12)

    def test_active_cancellation_after_rename_reaps_server_without_claiming_completion(self):
        self.interrupted('cancel')

    def test_active_timeout_after_rename_retains_locks_without_replay(self):
        self.interrupted('timeout')


if __name__ == '__main__':
    unittest.main()
