"""Destructive authority never comes from read/write grants or a stale scope."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
from unittest.mock import patch
import unittest

SERVER = Path(__file__).resolve().parents[1] / 'integrations/coda-filesystem/server.py'
SPEC = importlib.util.spec_from_file_location('destructive_filesystem', SERVER)
filesystem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filesystem)


class FilesystemDestructiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'allowed'
        self.root.mkdir()
        self.source = self.root / 'file.txt'
        self.source.write_bytes(b'original')
        self.config = self.base / 'permissions.json'
        self.configure()
        guard = patch.object(filesystem, 'CONFIG_PATH', self.config)
        guard.start()
        self.addCleanup(guard.stop)

    def configure(self, *, read=None, write=None, destructive=None):
        self.config.write_text(json.dumps({'read': read if read is not None else {
            'allowed_roots': [str(self.root)], 'blocked_paths': ['**/.env']},
            'write': write if write is not None else {'enabled': True, 'allowed_roots': [str(self.root)]},
            'destructive': destructive if destructive is not None else {
                'enabled': True, 'allowed_roots': [str(self.root)], 'require_confirmation': True}}), encoding='utf-8')

    def delete(self, path=None, **arguments):
        return filesystem.delete_path(str(path or self.source), **arguments)

    def error(self, result, code):
        self.assertTrue(result.is_error, result)
        self.assertEqual(result.structured_content['error']['code'], code, result)

    def folder(self):
        folder = self.root / 'folder'
        folder.mkdir()
        (folder / 'a.txt').write_bytes(b'a')
        (folder / 'b.txt').write_bytes(b'b')
        return folder

    def test_read_and_write_grants_cannot_delete_without_destructive_authority(self):
        self.configure(destructive={})
        self.error(self.delete(), 'destructive_disabled')
        self.assertEqual(self.source.read_bytes(), b'original')
        self.assertFalse((self.root / '.coda-lock').exists())
        self.configure(destructive={'enabled': True, 'allowed_roots': []})
        self.error(self.delete(), 'outside_allowed_roots')
        self.assertTrue(self.source.exists())

    def test_destructive_authority_does_not_require_read_or_general_write_grants(self):
        self.configure(read={'allowed_roots': [], 'blocked_paths': []}, write={})
        self.source.write_bytes(b'\0\xff' * 1048576)
        opening = Path.open
        def no_contents(path, *args, **kwargs):
            if path == self.source:
                raise AssertionError('Deleting a large binary file must not read its contents')
            return opening(path, *args, **kwargs)
        with patch.object(Path, 'open', no_contents):
            result = self.delete()
        self.assertFalse(result.is_error, result)
        self.assertFalse(self.source.exists())
        self.assertEqual(result.structured_content['files_removed'], 1)
        self.assertFalse((self.root / '.coda-lock').exists())

    def test_nonempty_folder_requires_recursive_opt_in_and_empty_folder_does_not(self):
        folder = self.folder()
        self.error(self.delete(folder), 'directory_not_empty')
        self.assertTrue((folder / 'a.txt').exists())
        self.assertFalse((self.root / '.coda-lock').exists())
        empty = self.root / 'empty'
        empty.mkdir()
        result = self.delete(empty)
        self.assertFalse(result.is_error, result)
        self.assertFalse(empty.exists())

    def test_recursive_delete_removes_only_preflighted_entries_and_reports_counts(self):
        folder = self.folder()
        nested = folder / 'nested'
        nested.mkdir()
        (nested / 'third').write_bytes(b'c')
        result = self.delete(folder, recursive=True)
        self.assertFalse(result.is_error, result)
        self.assertEqual(result.structured_content['files_removed'], 3)
        self.assertEqual(result.structured_content['directories_removed'], 2)
        self.assertFalse(folder.exists())
        self.assertTrue(self.source.exists())
        self.assertFalse((self.root / '.coda-lock').exists())

    def test_configured_roots_workspaces_and_their_ancestors_are_protected(self):
        self.error(self.delete(self.root, recursive=True), 'protected_path')
        folder = self.folder()
        workspace = folder / 'workspace'
        workspace.mkdir()
        self.configure(read={'allowed_roots': [str(self.root)], 'blocked_paths': [], 'workspace_roots': [str(workspace)]})
        self.error(self.delete(folder, recursive=True), 'protected_path')
        self.assertTrue((folder / 'a.txt').exists())
        self.assertTrue(workspace.exists())

    def test_blocked_descendant_aborts_preflight_without_disclosing_its_name(self):
        folder = self.folder()
        blocked = folder / '.env'
        blocked.write_bytes(b'private')
        result = self.delete(folder, recursive=True)
        self.error(result, 'blocked_path')
        self.assertNotIn('.env', '\n'.join(block.text for block in result.content))
        self.assertTrue(blocked.exists())
        self.assertTrue((folder / 'a.txt').exists())
        self.assertFalse((self.root / '.coda-lock').exists())

    def test_hardlink_aliases_are_refused_without_removing_either_name(self):
        alias = self.root / 'alias.txt'
        try:
            os.link(self.source, alias)
        except OSError:
            self.skipTest('Hard links unavailable on the fixture filesystem')
        self.error(self.delete(), 'unsafe_path')
        self.assertEqual(alias.read_bytes(), b'original')
        self.assertTrue(self.source.exists())

    def test_symbolic_link_descendants_are_not_followed_or_unlinked(self):
        folder = self.folder()
        alias = folder / 'alias'
        try:
            alias.symlink_to(self.source)
        except OSError:
            self.skipTest('Native symbolic links unavailable')
        self.error(self.delete(folder, recursive=True), 'unsafe_path')
        self.assertTrue(alias.is_symlink())
        self.assertTrue(self.source.exists())
        self.assertTrue((folder / 'a.txt').exists())

    @unittest.skipUnless(os.name == 'nt', 'Native Windows junction semantics')
    def test_junction_descendants_are_refused_before_any_deletion(self):
        folder = self.folder()
        actual = self.root / 'actual'
        actual.mkdir()
        (actual / 'notes').write_bytes(b'original')
        alias = folder / 'alias'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(actual)], capture_output=True, text=True)
        if made.returncode:
            self.skipTest('Junction creation unavailable')
        try:
            self.error(self.delete(folder, recursive=True), 'unsafe_path')
            self.assertEqual((actual / 'notes').read_bytes(), b'original')
            self.assertTrue((folder / 'a.txt').exists())
        finally:
            self.assertTrue(alias.resolve().is_relative_to(self.base))
            alias.rmdir()

    def test_entry_depth_time_and_malformed_argument_limits_prevent_deletion(self):
        folder = self.folder()
        nested = folder / 'nested'
        nested.mkdir()
        for limits in ({'max_entries': 1}, {'max_depth': 0}):
            self.error(self.delete(folder, recursive=True, **limits), 'resource_limit')
        with patch.object(filesystem, 'SEARCH_SECONDS', 0):
            self.error(self.delete(folder, recursive=True), 'resource_limit')
        for limits in ({'recursive': 1}, {'max_entries': True}, {'max_entries': 4097}, {'max_depth': 17}):
            self.error(self.delete(folder, **limits), 'invalid_arguments')
        self.error(filesystem.delete_path('relative'), 'invalid_arguments')
        self.assertTrue((folder / 'a.txt').exists())
        self.assertTrue(nested.exists())
        self.assertFalse((self.root / '.coda-lock').exists())

    def test_source_change_after_preflight_prevents_the_first_deletion(self):
        original = filesystem._destructive_manifest
        def change(*args):
            entries = original(*args)
            self.source.write_bytes(b'external change')
            return entries
        with patch.object(filesystem, '_destructive_manifest', side_effect=change):
            self.error(self.delete(), 'content_changed')
        self.assertEqual(self.source.read_bytes(), b'external change')
        self.assertFalse((self.root / '.coda-lock').exists())

    def test_permission_revocation_midway_reports_partial_outcome_without_replay(self):
        folder = self.folder()
        unlink = Path.unlink
        def revoke(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if path == folder / 'b.txt':
                self.configure(destructive={})
        with patch.object(Path, 'unlink', revoke):
            result = self.delete(folder, recursive=True)
        self.error(result, 'delete_incomplete')
        self.assertEqual(result.structured_content['files_removed'], 1)
        self.assertEqual(result.structured_content['cause_code'], 'permission_denied')
        self.assertTrue((folder / 'a.txt').exists())
        self.assertFalse((folder / 'b.txt').exists())
        self.assertGreater(result.structured_content['recovery_lock_count'], 0)

    def test_new_entries_are_not_added_to_an_already_approved_recursive_scope(self):
        folder = self.folder()
        unlink = Path.unlink
        def add_entry(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if path == folder / 'b.txt':
                (folder / 'new.txt').write_bytes(b'new user data')
        with patch.object(Path, 'unlink', add_entry):
            result = self.delete(folder, recursive=True)
        self.error(result, 'delete_incomplete')
        self.assertEqual(result.structured_content['files_removed'], 2)
        self.assertEqual((folder / 'new.txt').read_bytes(), b'new user data')
        self.assertTrue(folder.exists())
        self.error(self.delete(folder, recursive=True), 'mutation_in_progress')

    def test_recovery_lock_examples_have_bounded_output_and_explicit_truncation(self):
        folder = self.root / 'many'
        folder.mkdir()
        for index in range(25):
            (folder / f'd{index:02}').mkdir()
        rmdir = Path.rmdir
        def unavailable(path, *args, **kwargs):
            if path == folder / 'd24':
                raise OSError('simulated directory failure')
            return rmdir(path, *args, **kwargs)
        with patch.object(Path, 'rmdir', unavailable):
            result = self.delete(folder, recursive=True)
        self.error(result, 'delete_incomplete')
        data = result.structured_content
        self.assertGreater(data['recovery_lock_count'], len(data['recovery_paths']))
        self.assertTrue(data['recovery_paths_truncated'])
        self.assertLessEqual(len(data['recovery_paths']), 20)
        self.assertLessEqual(len(json.dumps(data['recovery_paths'], ensure_ascii=True)), 32768)

    def test_own_permission_file_cannot_be_rewritten_even_with_broad_grants(self):
        grant = {'allowed_roots': [str(self.base)], 'blocked_paths': []}
        self.configure(read=grant, write={'enabled': True, **grant}, destructive={'enabled': True, **grant})
        original = self.config.read_bytes()
        revision = hashlib.sha256(original).hexdigest()
        self.error(filesystem.append_file(str(self.config), ' ', revision), 'protected_path')
        self.error(filesystem.edit_file(str(self.config), original.decode(), '{}', revision), 'protected_path')
        self.error(self.delete(self.config), 'protected_path')
        self.error(filesystem.copy_path(str(self.source), str(self.config)), 'protected_path')
        self.assertEqual(self.config.read_bytes(), original)

    def test_running_installation_is_reserved_using_only_disposable_fixture_files(self):
        application = self.base / 'application'
        server_root = application / 'integrations' / 'coda-filesystem'
        server_root.mkdir(parents=True)
        (server_root / 'server.py').write_text('fixture', encoding='utf-8')
        (application / 'ai' / 'mcp').mkdir(parents=True)
        (application / 'ai' / 'mcp' / 'runtime.py').write_text('fixture', encoding='utf-8')
        runtime = application / 'coda_runtime.py'
        runtime.write_bytes(b'fixture runtime')
        (application / 'mcp_servers').mkdir()
        grant = {'allowed_roots': [str(self.base)], 'blocked_paths': []}
        self.configure(read=grant, write={'enabled': True, **grant}, destructive={'enabled': True, **grant})
        with patch.object(filesystem, '__file__', str(server_root / 'server.py')):
            self.error(filesystem.create_file(str(application / 'mcp_servers' / 'new.json'), '{}'), 'protected_path')
            self.error(filesystem.append_file(str(runtime), 'change', hashlib.sha256(runtime.read_bytes()).hexdigest()), 'protected_path')
            self.error(self.delete(application, recursive=True), 'protected_path')
        self.assertEqual(runtime.read_bytes(), b'fixture runtime')
        self.assertFalse((application / 'mcp_servers' / 'new.json').exists())

    def test_reserved_transaction_names_cannot_be_created_or_mutated_by_generic_tools(self):
        self.error(filesystem.create_file(str(self.root / '.coda-lock'), 'forged'), 'protected_path')
        backup = self.root / ('.coda-backup-' + 'a' * 32)
        backup.write_bytes(b'recovery')
        self.error(filesystem.append_file(str(backup), 'change', hashlib.sha256(b'recovery').hexdigest()), 'protected_path')
        self.error(self.delete(backup), 'protected_path')
        self.assertEqual(backup.read_bytes(), b'recovery')
        self.assertFalse((self.root / '.coda-lock').exists())

    def test_delete_locks_exclude_a_concurrent_text_mutation(self):
        folder = self.folder()
        paused = Event()
        proceed = Event()
        unlink = Path.unlink
        def wait_after_removal(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if path == folder / 'b.txt':
                paused.set()
                if not proceed.wait(8):
                    raise OSError('fixture release wait expired')
        results = []
        worker = Thread(target=lambda: results.append(self.delete(folder, recursive=True)), daemon=True)
        with patch.object(Path, 'unlink', wait_after_removal):
            worker.start()
            try:
                self.assertTrue(paused.wait(5))
                self.error(filesystem.append_file(str(folder / 'a.txt'), 'unexpected', hashlib.sha256(b'a').hexdigest()), 'mutation_in_progress')
                self.assertEqual((folder / 'a.txt').read_bytes(), b'a')
            finally:
                proceed.set()
                worker.join(8)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0].is_error, results[0])
        self.assertFalse(folder.exists())

    def host(self, *, mode='persistent', allowed=True):
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
        (definitions / 'files.json').write_text(json.dumps({
            'id': 'files', 'bundled_server': 'filesystem', 'trusted': True, 'enabled': True,
            'settings': {'config_file': str(self.config)}, 'session_mode': mode,
            'allowed_tools': ['delete_path'] if allowed else [], 'confirm_tools': [],
        }), encoding='utf-8')
        found = discover_definitions(definitions)
        self.assertEqual(found.errors, {})
        self.assertIsNone(found.servers[0].bundle_error)
        manager = LocalMCPManager(found.servers)
        runtime.configure_intent_router({}, mcp_manager=manager)
        self.addCleanup(runtime.shutdown_tools)
        return runtime

    def test_real_bundle_allows_destructive_only_grant_and_requires_one_use_confirmation(self):
        self.configure(read={'allowed_roots': [], 'blocked_paths': []}, write={})
        runtime = self.host(mode='operation')
        pending = runtime.run('mcp.files.delete_path ' + json.dumps({'path': str(self.source)}), {})
        self.assertIn('Permanently delete', pending.response_text)
        self.assertTrue(self.source.exists())
        self.assertFalse((self.root / '.coda-lock').exists())
        result = runtime.run('confirm', {})
        self.assertTrue(result.tool_result.success, result)
        self.assertFalse(self.source.exists())
        self.source.write_bytes(b'new user data')
        with patch.object(runtime, '_dispatch_intent', return_value=None), \
             patch.object(runtime, '_llm_fallback_enabled', return_value=False):
            runtime.run('confirm', {})
        self.assertEqual(self.source.read_bytes(), b'new user data')

    def test_real_confirmation_cannot_expand_target_or_recursive_scope(self):
        runtime = self.host()
        folder = self.folder()
        other = self.root / 'other'
        other.mkdir()
        (other / 'new.txt').write_bytes(b'not approved')
        arguments = {'path': str(folder), 'recursive': False}
        # Model a shared extractor result changing after the prompt. Approval
        # must execute the retained copy, not the caller's now-expanded object.
        with patch.object(runtime, 'prepare_arguments', return_value=arguments):
            runtime.run('mcp.files.delete_path {}', {})
        arguments.update(path=str(other), recursive=True)
        result = runtime.run('confirm', {})
        self.assertFalse(result.tool_result.success)
        self.assertEqual(result.tool_result.data['structuredContent']['error']['code'], 'directory_not_empty')
        self.assertTrue((folder / 'a.txt').exists())
        self.assertEqual((other / 'new.txt').read_bytes(), b'not approved')

    def test_real_allowlist_denial_cancel_and_revocation_leave_data_intact(self):
        runtime = self.host(allowed=False)
        request = 'mcp.files.delete_path ' + json.dumps({'path': str(self.source)})
        result = runtime.run(request, {})
        self.assertFalse(result.tool_result.success)
        self.assertEqual(result.tool_result.error.code, 'permission_denied')
        self.assertTrue(self.source.exists())
        # Reconfigure a separate explicitly allowed definition for approval tests.
        from dataclasses import replace
        from ai.mcp.runtime import LocalMCPManager
        manager = runtime._mcp_catalog.manager
        definition = manager._servers['files'].definition
        runtime.configure_intent_router({}, mcp_manager=LocalMCPManager([
            replace(definition, allowed_tools=frozenset({'delete_path'}))]))
        runtime.run(request, {})
        runtime.run('cancel', {})
        self.assertTrue(self.source.exists())
        runtime.run(request, {})
        self.configure(destructive={})
        result = runtime.run('confirm', {})
        self.assertFalse(result.tool_result.success)
        self.assertIn('disabled', result.response_text)
        self.assertTrue(self.source.exists())
        self.assertFalse((self.root / '.coda-lock').exists())

    def interrupted(self, action):
        import psutil
        from ai.intents import IntentRequest
        from ai.mcp.config import LocalServerDefinition
        from ai.mcp.runtime import LocalMCPManager
        folder = self.folder()
        marker = self.base / 'first-removed'
        wrapper = self.base / 'paused-delete.py'
        wrapper.write_text('''
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('paused_delete_fixture', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.CONFIG_PATH = Path(sys.argv[2])
original = Path.unlink
def paused(path, *args, **kwargs):
    result = original(path, *args, **kwargs)
    if path == Path(sys.argv[4]):
        marker = Path(sys.argv[3])
        pending = marker.with_suffix('.pending')
        pending.write_text(str(os.getpid()), encoding='utf-8')
        pending.replace(marker)
        time.sleep(60)
    return result
Path.unlink = paused
module.server.run(transport='stdio')
''', encoding='utf-8')
        definition = LocalServerDefinition('active', sys.executable,
            args=(str(wrapper), str(SERVER), str(self.config), str(marker), str(folder / 'b.txt')),
            trusted=True, allowed_tools=frozenset({'delete_path'}), session_mode='persistent',
            startup_timeout_seconds=15, timeout_seconds=10 if action == 'cancel' else 2)
        manager = LocalMCPManager([definition])
        cancelled = Event()
        worker = None
        results = []
        try:
            found = manager.discover('active')
            self.assertTrue(found.success, found.error)
            intent = next(intent for intent in found.data if intent.name == 'mcp.active.delete_path')
            request = IntentRequest(intent, 'Delete approved folder', 1,
                arguments={'path': str(folder), 'recursive': True})
            worker = Thread(target=lambda: results.append(manager.executor(intent.name).execute(
                request, cancel_event=cancelled, confirmed=True)), daemon=True)
            worker.start()
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                cancelled.wait(0.01)
            self.assertTrue(marker.exists(), 'One real entry must be removed before interruption.')
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
            self.assertTrue((folder / 'a.txt').exists())
            self.assertFalse((folder / 'b.txt').exists())
            self.assertTrue((self.root / '.coda-lock').exists())
            self.error(self.delete(folder, recursive=True), 'mutation_in_progress')
            self.assertTrue((folder / 'a.txt').exists())
        finally:
            cancelled.set()
            manager.close()
            if worker is not None:
                worker.join(12)

    def test_active_cancellation_can_leave_partial_deletion_but_never_replays(self):
        self.interrupted('cancel')

    def test_active_timeout_can_leave_partial_deletion_but_never_replays(self):
        self.interrupted('timeout')


if __name__ == '__main__':
    unittest.main()
