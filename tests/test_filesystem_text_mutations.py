"""Revision-bound text changes preserve bytes, permissions and recovery evidence."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from threading import Event, Thread
import unittest
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1] / 'integrations/coda-filesystem/server.py'
SPEC = importlib.util.spec_from_file_location('text_mutation_filesystem', SERVER)
filesystem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filesystem)


class FilesystemTextMutationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root / 'permissions.json'
        self.source = self.root / 'report.txt'
        self.source.write_bytes(b'first\r\nsecond\r\n')
        self.configure()
        guard = patch.object(filesystem, 'CONFIG_PATH', self.config)
        guard.start()
        self.addCleanup(guard.stop)

    def configure(self, *, read=None, write=None):
        self.config.write_text(json.dumps({'read': read if read is not None else {
            'allowed_roots': [str(self.root)], 'blocked_paths': ['**/permissions.json', '**/.env']},
            'write': write if write is not None else {'enabled': True, 'allowed_roots': [str(self.root)]}}), encoding='utf-8')

    def revision(self):
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def edit(self, old='second', new='edited', revision=None):
        return filesystem.edit_file(str(self.source), old, new, revision or self.revision())

    def artifacts(self):
        return sorted(path for path in self.root.iterdir() if path.name.startswith('.coda-'))

    def error(self, result, code):
        self.assertTrue(result.is_error, result)
        self.assertEqual(result.structured_content['error']['code'], code, result)

    def test_edit_and_append_preserve_exact_crlf_utf8_and_return_new_revisions(self):
        self.source.write_bytes('\ufefffirst\r\ncaf\u00e9\r\n'.encode('utf-8'))
        observation = filesystem.read_file(str(self.source), include_revision=True)
        self.assertEqual(observation.structured_content['sha256'], self.revision())
        self.assertIn('\r\n', observation.content[0].text)
        result = self.edit('caf\u00e9', 'changed')
        self.assertFalse(result.is_error, result)
        self.assertEqual(self.source.read_bytes(), '\ufefffirst\r\nchanged\r\n'.encode('utf-8'))
        self.assertEqual(result.structured_content['sha256'], self.revision())
        result = filesystem.append_file(str(self.source), 'next\r\n', self.revision())
        self.assertFalse(result.is_error, result)
        self.assertTrue(self.source.read_bytes().endswith(b'changed\r\nnext\r\n'))
        self.assertEqual(self.artifacts(), [])

    def test_stale_revision_and_ambiguous_or_missing_text_leave_original_intact(self):
        original = self.source.read_bytes()
        self.error(self.edit(revision='0' * 64), 'content_changed')
        self.error(self.edit('absent', 'new'), 'ambiguous_edit')
        self.source.write_bytes(b'repeated repeated')
        self.error(self.edit('repeated', 'new'), 'ambiguous_edit')
        self.assertEqual(self.source.read_bytes(), b'repeated repeated')
        self.assertEqual(self.artifacts(), [])
        self.source.write_bytes(original)

    def test_read_and_write_authority_are_independent(self):
        original = self.source.read_bytes()
        revision = self.revision()
        self.configure(write={})
        self.error(self.edit(revision=revision), 'write_disabled')
        self.configure(read={'allowed_roots': [], 'blocked_paths': []})
        self.error(self.edit(revision=revision), 'outside_allowed_roots')
        self.configure(write={'enabled': True, 'allowed_roots': []})
        self.error(self.edit(revision=revision), 'outside_allowed_roots')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.artifacts(), [])

    def test_blocked_targets_links_and_hardlink_aliases_are_refused(self):
        hidden = self.root / '.env'
        hidden.write_bytes(self.source.read_bytes())
        self.error(filesystem.append_file(str(hidden), 'text', self.revision()), 'blocked_path')
        alias = self.root / 'alias.txt'
        try:
            os.link(self.source, alias)
        except OSError:
            self.skipTest('Hard links unavailable on this filesystem')
        self.error(self.edit(), 'unsafe_path')
        self.assertEqual(alias.read_bytes(), self.source.read_bytes())

    def test_source_change_during_staging_is_not_overwritten(self):
        original_stage = filesystem._stage_security
        def concurrent_write(*args):
            original_stage(*args)
            self.source.write_bytes(b'changed externally')
        with patch.object(filesystem, '_stage_security', side_effect=concurrent_write):
            self.error(self.edit(), 'content_changed')
        self.assertEqual(self.source.read_bytes(), b'changed externally')
        self.assertEqual(self.artifacts(), [])

    def test_revoked_write_permission_during_staging_prevents_replacement(self):
        original = self.source.read_bytes()
        original_stage = filesystem._stage_security
        def revoke(*args):
            original_stage(*args)
            self.configure(write={})
        with patch.object(filesystem, '_stage_security', side_effect=revoke):
            self.error(self.edit(), 'write_disabled')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.artifacts(), [])

    def test_failed_staged_flush_does_not_touch_original(self):
        original = self.source.read_bytes()
        fsync = os.fsync
        calls = []
        def fail_stage(descriptor):
            calls.append(descriptor)
            if len(calls) == 2:
                raise OSError('simulated staged flush failure')
            return fsync(descriptor)
        with patch.object(filesystem.os, 'fsync', side_effect=fail_stage):
            self.error(self.edit(), 'os_error')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.artifacts(), [])

    def test_security_copy_failure_happens_before_any_staged_content_is_written(self):
        original = self.source.read_bytes()
        def denied(_source, staged, _descriptor, _metadata):
            self.assertEqual(staged.read_bytes(), b'')
            raise PermissionError('simulated security-copy failure')
        with patch.object(filesystem, '_stage_security', side_effect=denied):
            self.error(self.edit(), 'permission_denied')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.artifacts(), [])

    def test_cleanup_failure_reports_committed_change_without_inviting_replay(self):
        from ai.tools.models import ToolResult
        from ai.tools.presentation import prepare_tool_response
        original = self.source.read_bytes()
        unlink = Path.unlink
        def locked(artifact, *args, **options):
            if artifact.parent == self.root and artifact.name.startswith('.coda-backup'):
                raise PermissionError('simulated backup cleanup lock')
            return unlink(artifact, *args, **options)
        with patch.object(Path, 'unlink', locked):
            result = self.edit()
        self.assertFalse(result.is_error, result)
        self.assertIn(b'edited', self.source.read_bytes())
        self.assertEqual(Path(result.structured_content['recovery_paths'][0]).read_bytes(), original)
        speech = prepare_tool_response(ToolResult(True, result.model_dump(mode='json', by_alias=True)))
        self.assertIn('Updated the file', speech)
        self.assertIn('cleanup', speech)

    def test_partial_replacement_failure_retains_old_and_new_bytes_for_recovery(self):
        original = self.source.read_bytes()
        def partial(target, _staged, backup):
            os.replace(target, backup)
            raise OSError('simulated partial replacement failure')
        with patch.object(filesystem, '_replace_text', side_effect=partial):
            result = self.edit()
        self.error(result, 'mutation_incomplete')
        self.assertTrue(result.structured_content['target_may_have_changed'])
        paths = [Path(path) for path in result.structured_content['recovery_paths']]
        self.assertEqual(len(paths), 3)
        self.assertEqual(next(path for path in paths if path.name.startswith('.coda-backup')).read_bytes(), original)
        self.assertIn(b'edited', next(path for path in paths if path.suffix == '.tmp').read_bytes())
        self.assertIn('Recovery files retained', '\n'.join(block.text for block in result.content))

    def test_competing_mutation_cannot_remove_the_first_transaction_lock(self):
        staged = Event()
        proceed = Event()
        original_stage = filesystem._stage_security
        def pause(*args):
            original_stage(*args)
            staged.set()
            if not proceed.wait(8):
                raise OSError('fixture staging wait expired')
        results = []
        revision = self.revision()
        worker = Thread(target=lambda: results.append(self.edit(revision=revision)), daemon=True)
        with patch.object(filesystem, '_stage_security', side_effect=pause):
            worker.start()
            try:
                self.assertTrue(staged.wait(5))
                alias = str(self.source).upper() if os.name == 'nt' else str(self.source)
                self.error(filesystem.append_file(alias, 'extra', revision), 'mutation_in_progress')
                other = self.root / 'other.txt'
                other.write_bytes(b'other')
                self.error(filesystem.append_file(str(other), 'extra', hashlib.sha256(b'other').hexdigest()), 'mutation_in_progress')
                self.assertEqual(other.read_bytes(), b'other')
                self.assertTrue(any(path.name.startswith('.coda-lock') for path in self.artifacts()))
            finally:
                proceed.set()
                worker.join(8)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0].is_error, results[0])
        self.assertEqual(self.source.read_bytes(), b'first\r\nedited\r\n')
        self.assertEqual(self.artifacts(), [])

    def test_malformed_text_hashes_ranges_and_byte_limits_are_refused(self):
        original = self.source.read_bytes()
        self.error(filesystem.edit_file(str(self.source), '', 'new', self.revision()), 'invalid_arguments')
        self.error(filesystem.append_file(str(self.source), 'text', 'not-a-hash'), 'invalid_arguments')
        self.error(filesystem.append_file(str(self.source), '\ud800', self.revision()), 'invalid_encoding')
        self.error(filesystem.append_file(str(self.source), '\u00e9' * 524288, self.revision()), 'content_too_large')
        self.error(filesystem.read_file(str(self.source), max_bytes=4, include_revision=True), 'invalid_arguments')
        self.assertEqual(self.source.read_bytes(), original)
        self.source.write_bytes(b'x' * 1048577)
        self.error(self.edit(revision=self.revision()), 'content_too_large')
        self.assertEqual(self.artifacts(), [])

    def test_file_mode_and_native_windows_acl_are_preserved(self):
        if os.name != 'nt':
            self.source.chmod(0o640)
            self.assertFalse(self.edit().is_error)
            self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o640)
            return
        import subprocess
        quoted = str(self.source).replace("'", "''")
        # A PowerShell 7 parent can export module paths incompatible with the
        # Windows PowerShell child. Let the child build its own built-in paths.
        environment = {name: value for name, value in os.environ.items() if name.casefold() != 'psmodulepath'}
        # Use a private explicit DACL so inheriting the parent ACL would be a
        # meaningful regression, rather than comparing identical defaults.
        protect = (f"$path='{quoted}'; $acl=Get-Acl -LiteralPath $path; "
                   "$acl.SetAccessRuleProtection($true,$false); "
                   "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; "
                   "$rule=[System.Security.AccessControl.FileSystemAccessRule]::new($sid,"
                   "[System.Security.AccessControl.FileSystemRights]::FullControl,"
                   "[System.Security.AccessControl.AccessControlType]::Allow); "
                   "$acl.SetAccessRule($rule); Set-Acl -LiteralPath $path -AclObject $acl")
        protected = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', protect],
                                   check=False, capture_output=True, text=True, env=environment,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(protected.returncode, 0, protected.stdout + protected.stderr)
        def acl():
            return subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                f"(Get-Acl -LiteralPath '{quoted}').Sddl"], check=True, capture_output=True,
                text=True, env=environment, creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        original = acl()
        result = self.edit()
        self.assertFalse(result.is_error, result)
        self.assertEqual(acl(), original)

    @unittest.skipUnless(sys.platform == 'linux', 'Native Linux POSIX ACL inheritance')
    def test_parent_default_acl_cannot_broaden_the_replacement_file_acl(self):
        import errno
        import struct
        entries = [(1, 7, 0xffffffff), (2, 4, 12345), (4, 0, 0xffffffff),
                   (16, 4, 0xffffffff), (32, 0, 0xffffffff)]
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in entries)
        self.source.chmod(0o640)
        try:
            getattr(os, 'setxattr')(self.root, 'system.posix_acl_default', acl)
        except OSError as error:
            if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                self.skipTest('The fixture filesystem does not support POSIX ACLs')
            raise
        result = self.edit()
        self.assertFalse(result.is_error, result)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o640)
        self.assertNotIn('system.posix_acl_access', getattr(os, 'listxattr')(self.source))

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
            allowed_tools=frozenset({'read_file', 'edit_file', 'append_file'}),
            session_mode=mode, startup_timeout_seconds=15)
        manager = LocalMCPManager([definition])
        runtime.configure_intent_router({}, mcp_manager=manager)
        self.addCleanup(runtime.shutdown_tools)
        return runtime, manager

    def test_real_host_revision_read_and_confirmed_mutation_share_runtime(self):
        runtime, _ = self.host()
        def command(name, **arguments):
            return runtime.run('mcp.files.' + name + ' ' + json.dumps(arguments), {})
        read = command('read_file', path=str(self.source), include_revision=True)
        self.assertTrue(read.tool_result.success, read)
        self.assertNotIn('sha256', read.response_text)
        revision = read.tool_result.data['structuredContent']['sha256']
        pending = command('edit_file', path=str(self.source), old_text='second', new_text='confirmed', expected_sha256=revision)
        self.assertIn('Replace one occurrence', pending.response_text)
        self.assertIn(str(self.source), pending.response_text)
        self.assertEqual(self.source.read_bytes(), b'first\r\nsecond\r\n')
        completed = runtime.run('confirm', {})
        self.assertTrue(completed.tool_result.success, completed)
        self.assertEqual(self.source.read_bytes(), b'first\r\nconfirmed\r\n')
        revision = completed.tool_result.data['structuredContent']['sha256']
        command('append_file', path=str(self.source), content='cancelled', expected_sha256=revision)
        runtime.run('cancel', {})
        self.assertEqual(self.source.read_bytes(), b'first\r\nconfirmed\r\n')
        self.assertIsNone(runtime._pending_confirmation)

    def test_source_change_while_waiting_for_confirmation_is_refused(self):
        runtime, _ = self.host(mode='operation')
        revision = self.revision()
        runtime.run('mcp.files.append_file ' + json.dumps({'path': str(self.source),
                    'content': 'unexpected', 'expected_sha256': revision}), {})
        self.source.write_bytes(b'external change')
        result = runtime.run('confirm', {})
        self.assertFalse(result.tool_result.success)
        self.assertIn('file has changed', result.response_text)
        self.assertEqual(self.source.read_bytes(), b'external change')
        self.assertEqual(self.artifacts(), [])

    def interrupted(self, action):
        import psutil
        from ai.intents import IntentRequest
        from ai.mcp.config import LocalServerDefinition
        from ai.mcp.runtime import LocalMCPManager
        marker = self.root / 'staged'
        wrapper = self.root / 'paused-mutation.py'
        wrapper.write_text('''
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('paused_mutation_fixture', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.CONFIG_PATH = Path(sys.argv[2])
original = module._replace_text
def paused(*args):
    marker = Path(sys.argv[3])
    pending = marker.with_suffix('.pending')
    pending.write_text(str(os.getpid()), encoding='utf-8')
    pending.replace(marker)
    time.sleep(60)
    return original(*args)
module._replace_text = paused
module.server.run(transport='stdio')
''', encoding='utf-8')
        definition = LocalServerDefinition('active', sys.executable,
            args=(str(wrapper), str(SERVER), str(self.config), str(marker)), trusted=True,
            allowed_tools=frozenset({'edit_file'}), session_mode='persistent',
            startup_timeout_seconds=15, timeout_seconds=10 if action == 'cancel' else 2)
        manager = LocalMCPManager([definition])
        cancelled = Event()
        worker = None
        results = []
        original = self.source.read_bytes()
        try:
            found = manager.discover('active')
            self.assertTrue(found.success, found.error)
            intent = next(intent for intent in found.data if intent.name == 'mcp.active.edit_file')
            request = IntentRequest(intent, 'Edit text', 1, arguments={'path': str(self.source),
                    'old_text': 'second', 'new_text': 'edited', 'expected_sha256': self.revision()})
            worker = Thread(target=lambda: results.append(manager.executor(intent.name).execute(
                request, cancel_event=cancelled, confirmed=True)), daemon=True)
            worker.start()
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                cancelled.wait(0.01)
            self.assertTrue(marker.exists(), 'Staging must finish before interruption.')
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
            self.assertEqual(self.source.read_bytes(), original)
            self.assertEqual(len(self.artifacts()), 2)
            self.error(self.edit(), 'mutation_in_progress')
            self.assertEqual(len(self.artifacts()), 2)
        finally:
            cancelled.set()
            manager.close()
            if worker is not None:
                worker.join(12)

    def test_cancellation_after_staging_retains_evidence_and_does_not_replay(self):
        self.interrupted('cancel')

    def test_timeout_after_staging_retains_evidence_and_does_not_replay(self):
        self.interrupted('timeout')


if __name__ == '__main__':
    unittest.main()
