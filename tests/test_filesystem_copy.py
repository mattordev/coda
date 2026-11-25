"""Copying preserves sources and existing destinations within independent grants."""

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
SPEC = importlib.util.spec_from_file_location('copy_filesystem', SERVER)
filesystem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filesystem)


class FilesystemCopyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.source = self.base / 'source'
        self.source.mkdir()
        self.output = self.base / 'output'
        self.output.mkdir()
        self.target = self.output / 'copied'
        self.config = self.base / 'permissions.json'
        self.configure()
        guard = patch.object(filesystem, 'CONFIG_PATH', self.config)
        guard.start()
        self.addCleanup(guard.stop)

    def configure(self, *, read=None, write=None):
        self.config.write_text(json.dumps({'read': read if read is not None else {
            'allowed_roots': [str(self.source)], 'blocked_paths': ['**/.env']},
            'write': write if write is not None else {'enabled': True, 'allowed_roots': [str(self.output)]}}), encoding='utf-8')

    def copy(self, source=None, destination=None, **limits):
        return filesystem.copy_path(str(source or self.source), str(destination or self.target), **limits)

    def error(self, result, code):
        self.assertTrue(result.is_error, result)
        self.assertEqual(result.structured_content['error']['code'], code, result)

    def test_binary_files_empty_directories_and_nested_tree_copy_exact_bytes(self):
        (self.source / 'binary').write_bytes(b'\0\xff\x80binary')
        (self.source / 'empty').mkdir()
        (self.source / 'nested').mkdir()
        (self.source / 'nested' / 'notes').write_bytes(b'first\r\nsecond\r\n')
        result = self.copy()
        self.assertFalse(result.is_error, result)
        self.assertEqual((self.target / 'binary').read_bytes(), b'\0\xff\x80binary')
        self.assertTrue((self.target / 'empty').is_dir())
        self.assertEqual((self.target / 'nested' / 'notes').read_bytes(), b'first\r\nsecond\r\n')
        self.assertTrue((self.source / 'binary').exists())
        self.assertEqual(result.structured_content['entries_copied'], 5)
        self.assertEqual(list(self.output.glob('.coda-copy-*')), [])

    def test_single_file_copy_and_existing_destinations_never_overwrite(self):
        source = self.source / 'notes'
        source.write_bytes(b'original')
        self.assertFalse(self.copy(source).is_error)
        self.assertEqual(self.target.read_bytes(), b'original')
        source.write_bytes(b'changed')
        self.error(self.copy(source), 'already_exists')
        self.assertEqual(self.target.read_bytes(), b'original')
        destination = self.output / 'existing-dir'
        destination.mkdir()
        self.error(self.copy(source, destination), 'already_exists')
        self.assertTrue(destination.is_dir())

    def test_read_and_write_grants_are_independent_and_checked_before_creation(self):
        (self.source / 'notes').write_bytes(b'original')
        self.configure(write={})
        self.error(self.copy(), 'write_disabled')
        self.configure(read={'allowed_roots': [], 'blocked_paths': []})
        self.error(self.copy(), 'outside_allowed_roots')
        self.configure(write={'enabled': True, 'allowed_roots': [str(self.source)]})
        self.error(self.copy(), 'outside_allowed_roots')
        self.assertEqual(list(self.output.iterdir()), [])

    def test_blocked_descendant_and_blocked_destination_names_abort_before_writing(self):
        (self.source / '.env').write_bytes(b'private')
        denied = self.copy()
        self.error(denied, 'blocked_path')
        self.assertNotIn('.env', '\n'.join(block.text for block in denied.content))
        self.assertEqual(list(self.output.iterdir()), [])
        self.error(self.copy(self.source / '.env', self.output / 'public'), 'blocked_path')
        (self.source / '.env').unlink()
        (self.source / 'notes').write_bytes(b'public')
        self.error(self.copy(self.source / 'notes', self.output / '.env'), 'blocked_path')

    def test_directory_self_descendant_copy_is_rejected(self):
        self.configure(write={'enabled': True, 'allowed_roots': [str(self.base)]})
        self.error(self.copy(destination=self.source / 'inside'), 'invalid_arguments')
        self.assertFalse((self.source / 'inside').exists())

    def test_limits_and_malformed_requests_fail_before_staging(self):
        nested = self.source / 'nested'
        nested.mkdir()
        (nested / 'notes').write_bytes(b'12345')
        for limits in ({'max_entries': 1}, {'max_depth': 0}, {'max_bytes': 4}):
            with self.subTest(limits=limits):
                self.error(self.copy(**limits), 'resource_limit')
        for limits in ({'max_entries': True}, {'max_entries': 4097}, {'max_bytes': 0}, {'max_depth': 17}):
            with self.subTest(limits=limits):
                self.error(self.copy(**limits), 'invalid_arguments')
        self.error(filesystem.copy_path('relative', str(self.target)), 'invalid_arguments')
        self.error(filesystem.copy_path(str(self.source), str(self.output / '\ud800')), 'invalid_arguments')
        self.assertEqual(list(self.output.iterdir()), [])

    def test_new_destination_created_at_publication_is_preserved_atomically(self):
        (self.source / 'notes').write_bytes(b'new data')
        rename = filesystem._rename_no_replace
        def competitor(source, destination):
            destination.mkdir()
            (destination / 'existing').write_bytes(b'do not overwrite')
            rename(source, destination)
        with patch.object(filesystem, '_rename_no_replace', side_effect=competitor):
            result = self.copy()
        self.error(result, 'copy_incomplete')
        self.assertEqual(result.structured_content['cause_code'], 'already_exists')
        self.assertEqual((self.target / 'existing').read_bytes(), b'do not overwrite')
        self.assertTrue(Path(result.structured_content['staging_path']).is_dir())

    def test_source_change_and_permission_revocation_retain_staging_without_publishing(self):
        (self.source / 'notes').write_bytes(b'original')
        copying = filesystem._copy_file_data
        for change in ('source', 'permission'):
            with self.subTest(change=change):
                self.configure()
                def interrupted(*args):
                    count = copying(*args)
                    if change == 'source':
                        (self.source / 'notes').write_bytes(b'changed externally')
                    else:
                        self.configure(write={})
                    return count
                with patch.object(filesystem, '_copy_file_data', side_effect=interrupted):
                    result = self.copy(destination=self.output / change)
                self.error(result, 'copy_incomplete')
                self.assertFalse((self.output / change).exists())
                self.assertTrue(Path(result.structured_content['staging_path']).exists())
                self.assertTrue((self.source / 'notes').exists())

    def test_expired_deadline_prevents_writing(self):
        with patch.object(filesystem, 'SEARCH_SECONDS', 0):
            self.error(self.copy(), 'resource_limit')
        self.assertEqual(list(self.output.iterdir()), [])

    def test_linked_descendants_are_never_followed(self):
        (self.source / 'actual').write_bytes(b'original')
        try:
            (self.source / 'alias').symlink_to(self.source / 'actual')
        except OSError:
            self.skipTest('Native symbolic links unavailable')
        self.error(self.copy(), 'unsafe_path')
        self.assertEqual(list(self.output.iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Native Windows junction semantics')
    def test_junctions_are_refused_even_to_allowed_targets(self):
        actual = self.source / 'actual'
        actual.mkdir()
        (actual / 'notes').write_bytes(b'original')
        alias = self.source / 'alias'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(actual)], capture_output=True, text=True)
        if made.returncode:
            self.skipTest('Junction creation unavailable')
        try:
            self.error(self.copy(), 'unsafe_path')
            self.assertEqual(list(self.output.iterdir()), [])
        finally:
            self.assertTrue(alias.resolve().is_relative_to(self.base))
            alias.rmdir()

    def host(self, *, mode='persistent'):
        from ai.mcp.config import LocalServerDefinition
        from ai.mcp.runtime import LocalMCPManager
        import utils.on_command as runtime
        import utils.llm_service as history
        for name in ('_mcp_catalog', '_pending_confirmation', '_intent_registry', '_intent_router'):
            guard = patch.object(runtime, name, None)
            guard.start()
            self.addCleanup(guard.stop)
        history._reset_conversation()
        self.addCleanup(history._reset_conversation)
        definition = LocalServerDefinition('files', sys.executable,
            args=(str(SERVER), '--config', str(self.config)), trusted=True,
            bundled_server='filesystem', settings_json=json.dumps({'config_file': str(self.config)}),
            allowed_tools=frozenset({'copy_path'}), session_mode=mode, startup_timeout_seconds=15)
        manager = LocalMCPManager([definition])
        runtime.configure_intent_router({}, mcp_manager=manager)
        self.addCleanup(runtime.shutdown_tools)
        return runtime

    def test_real_host_confirmation_binds_source_and_destination_and_can_cancel(self):
        runtime = self.host()
        (self.source / 'notes').write_bytes(b'original')
        command = 'mcp.files.copy_path ' + json.dumps({'source': str(self.source), 'destination': str(self.target)})
        pending = runtime.run(command, {})
        self.assertIn(str(self.source), pending.response_text)
        self.assertIn(str(self.target), pending.response_text)
        self.assertEqual(list(self.output.iterdir()), [])
        runtime.run('cancel', {})
        self.assertEqual(list(self.output.iterdir()), [])
        runtime.run(command, {})
        completed = runtime.run('confirm', {})
        self.assertTrue(completed.tool_result.success, completed)
        self.assertIn('Copied the folder', completed.response_text)
        self.assertEqual((self.target / 'notes').read_bytes(), b'original')

    def test_real_host_approval_does_not_bypass_revoked_write_policy(self):
        runtime = self.host()
        runtime.run('mcp.files.copy_path ' + json.dumps({'source': str(self.source), 'destination': str(self.target)}), {})
        self.configure(write={})
        result = runtime.run('confirm', {})
        self.assertFalse(result.tool_result.success)
        self.assertIn('disabled', result.response_text)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_real_operation_mode_copies_binary_file_without_reading_it_into_conversation(self):
        runtime = self.host(mode='operation')
        source = self.source / 'binary.bin'
        data = b'\0\xffbinary bytes' * 10000
        source.write_bytes(data)
        runtime.run('mcp.files.copy_path ' + json.dumps({'source': str(source), 'destination': str(self.target)}), {})
        result = runtime.run('confirm', {})
        self.assertTrue(result.tool_result.success, result)
        self.assertEqual(self.target.read_bytes(), data)
        self.assertEqual(result.tool_result.data['structuredContent']['bytes_copied'], len(data))
        self.assertNotIn('binary bytes', result.response_text)

    def interrupted(self, action):
        import psutil
        from ai.intents import IntentRequest
        from ai.mcp.config import LocalServerDefinition
        from ai.mcp.runtime import LocalMCPManager
        content = b'original bytes\n' * 20000
        (self.source / 'notes').write_bytes(content)
        marker = self.base / 'stream-started'
        wrapper = self.base / 'paused-copy.py'
        wrapper.write_text('''
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('paused_copy_fixture', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.CONFIG_PATH = Path(sys.argv[2])
original = module._copy_file_data
class PausedWriter:
    def __init__(self, output): self.output = output
    def __getattr__(self, name): return getattr(self.output, name)
    def write(self, data):
        count = self.output.write(data)
        self.output.flush()
        os.fsync(self.output.fileno())
        marker = Path(sys.argv[3])
        pending = marker.with_suffix('.pending')
        pending.write_text(str(os.getpid()), encoding='utf-8')
        pending.replace(marker)
        time.sleep(60)
        return count
def paused(source, staged, output, *args):
    return original(source, staged, PausedWriter(output), *args)
module._copy_file_data = paused
module.server.run(transport='stdio')
''', encoding='utf-8')
        definition = LocalServerDefinition('active', sys.executable,
            args=(str(wrapper), str(SERVER), str(self.config), str(marker)), trusted=True,
            allowed_tools=frozenset({'copy_path'}), session_mode='persistent',
            startup_timeout_seconds=15, timeout_seconds=10 if action == 'cancel' else 2)
        manager = LocalMCPManager([definition])
        cancelled = Event()
        worker = None
        results = []
        try:
            found = manager.discover('active')
            self.assertTrue(found.success, found.error)
            intent = next(intent for intent in found.data if intent.name == 'mcp.active.copy_path')
            request = IntentRequest(intent, 'Copy files', 1,
                arguments={'source': str(self.source), 'destination': str(self.target)})
            worker = Thread(target=lambda: results.append(manager.executor(intent.name).execute(
                request, cancel_event=cancelled, confirmed=True)), daemon=True)
            worker.start()
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                cancelled.wait(0.01)
            self.assertTrue(marker.exists(), 'The real first chunk must be written before interruption.')
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
            self.assertFalse(self.target.exists())
            self.assertEqual((self.source / 'notes').read_bytes(), content)
            stages = list(self.output.glob('.coda-copy-*'))
            self.assertEqual(len(stages), 1)
            partial = (stages[0] / 'notes').read_bytes()
            self.assertGreater(len(partial), 0)
            self.assertLess(len(partial), len(content))
            self.assertEqual(partial, content[:len(partial)])
            # A later explicit copy can succeed without deleting earlier evidence.
            self.assertFalse(self.copy().is_error)
            self.assertEqual((stages[0] / 'notes').read_bytes(), partial)
        finally:
            cancelled.set()
            manager.close()
            if worker is not None:
                worker.join(12)

    def test_active_copy_cancellation_retains_partial_staging_and_reaps_server(self):
        self.interrupted('cancel')

    def test_active_copy_timeout_retains_partial_staging_and_reaps_server(self):
        self.interrupted('timeout')


if __name__ == '__main__':
    unittest.main()
