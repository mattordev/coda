"""Conversation selection uses real filesystem policy and the common dispatcher."""

import asyncio
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from ai.mcp.config import LocalServerDefinition
from ai.mcp.runtime import LocalMCPManager
from ai.tools.models import ToolResult
from ai.tools.presentation import prepare_full_tool_output, prepare_tool_response
import utils.llm_service as history
import utils.on_command as runtime

SERVER = Path(__file__).resolve().parents[1] / 'integrations/coda-filesystem/server.py'
SPEC = importlib.util.spec_from_file_location('conversation_filesystem', SERVER)
filesystem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filesystem)


class FilesystemConversationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root / 'permissions.json'
        self.configure_policy()
        for name, content, modified in (('old-report.md', 'old', 1000),
                                        ('new-report.md', 'new', 2000), ('notes.txt', 'notes', 500)):
            path = self.root / name
            path.write_text(content, encoding='utf-8')
            os.utime(path, (modified, modified))
        self.definition = LocalServerDefinition('files', sys.executable,
            args=(str(SERVER), '--config', str(self.config)), trusted=True,
            bundled_server='filesystem', settings_json=json.dumps({'config_file': str(self.config)}),
            allowed_tools=frozenset({'list_directory', 'search_files', 'read_file', 'get_file_metadata'}),
            session_mode='persistent')
        for target, value in (("CONFIG_PATH", self.config),):
            guard = patch.object(filesystem, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        for target, value in (('_mcp_catalog', None), ('_pending_confirmation', None),
                              ('_intent_registry', None), ('_intent_router', None)):
            guard = patch.object(runtime, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        history._reset_conversation()
        self.addCleanup(history._reset_conversation)
        no_model = patch.object(history, 'call_with_messages', side_effect=AssertionError('No model required'))
        no_model.start()
        self.addCleanup(no_model.stop)
        self.calls = []
        schemas = [tool.model_dump(mode='json', by_alias=True) for tool in asyncio.run(filesystem.server.list_tools())]

        def transport_call(_definition, operation, **options):
            if operation == 'discover':
                return ToolResult(True, {'tools': schemas})
            self.calls.append(options)
            raw = getattr(filesystem, options['tool_name'])(**options['arguments'])
            return ToolResult(not raw.is_error, raw.model_dump(mode='json', by_alias=True))

        self.transport = Mock()
        self.transport.request.side_effect = transport_call
        self.manager = LocalMCPManager([self.definition], transport=self.transport)
        runtime.configure_intent_router({}, mcp_manager=self.manager)
        self.addCleanup(runtime.shutdown_tools)

    def configure_policy(self, **changes):
        self.config.write_text(json.dumps({'read': {
            'allowed_roots': [str(self.root)], 'blocked_paths': ['**/permissions.json', '**/.env'], **changes,
        }}), encoding='utf-8')

    def command(self, tool, **arguments):
        return runtime.run('mcp.files.' + tool + ' ' + json.dumps(arguments), {})

    def listing(self):
        return self.command('list_directory', path=str(self.root))

    def test_list_filter_then_read_recent_report_uses_only_observed_folder(self):
        self.assertIn('3 files', self.listing().response_text)
        count = len(self.calls)
        filtered = runtime.run('find Markdown files', {})
        self.assertIn('retained immediate-folder listing', filtered.response_text)
        self.assertIn('2 files', filtered.response_text)
        self.assertEqual(len(self.calls), count)
        result = runtime.run('read the newest report', {})
        self.assertEqual(result.response_text, 'new')
        self.assertEqual(self.calls[-1]['arguments'], {'path': str(self.root / 'new-report.md')})

    def test_search_is_a_fresh_call_and_duplicate_basenames_require_clarification(self):
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'new-report.md').write_text('nested', encoding='utf-8')
        self.listing()
        result = runtime.run('search that folder for markdown files', {})
        self.assertIn('3 matching files', result.response_text)
        self.assertEqual(self.calls[-1]['tool_name'], 'search_files')
        count = len(self.calls)
        result = runtime.run('read new-report.md', {})
        self.assertIn('ambiguous', result.response_text)
        self.assertEqual(len(self.calls), count)

    def test_ambiguous_pronoun_never_reads_a_random_file(self):
        self.listing()
        count = len(self.calls)
        self.assertIn('Which file', runtime.run('read that one', {}).response_text)
        self.assertEqual(len(self.calls), count)
        self.assertEqual(runtime.run('read new-report.md', {}).response_text, 'new')

    def test_policy_change_invalidates_even_retained_name_filtering(self):
        self.listing()
        count = len(self.calls)
        self.configure_policy(allowed_roots=[])
        self.assertIn('Which folder', runtime.run('show filenames', {}).response_text)
        self.assertEqual(len(self.calls), count)
        self.assertIsNone(history.get_tool_follow_up_context())

    def test_grant_revocation_is_rechecked_by_dispatcher(self):
        self.listing()
        # Configuration revision changes invalidate focus; this also prevents a fresh invocation.
        self.manager.replace_definition(replace(self.definition, allowed_tools=frozenset({'list_directory'})))
        count = len(self.calls)
        self.assertIn('Which folder', runtime.run('read the newest file', {}).response_text)
        self.assertEqual(len(self.calls), count)

    def test_fresh_read_still_consults_permission_decider(self):
        self.listing()
        self.manager._permission_decider = lambda _server, tool, _request: 'deny' if tool == 'read_file' else 'allow'
        count = len(self.calls)
        denied = runtime.run('read the newest report', {})
        self.assertFalse(denied.tool_result.success)
        self.assertEqual(denied.tool_result.error.code, 'permission_denied')
        self.assertEqual(len(self.calls), count)

    def test_partial_and_tied_newest_results_cannot_choose_a_file(self):
        self.listing()
        previous = history.get_tool_follow_up_context()
        data = json.loads(json.dumps(previous.data))
        data['structuredContent']['truncated'] = True
        history.record_tool_exchange('partial listing', replace(previous, data=data), 'partial')
        count = len(self.calls)
        self.assertIn('partial', runtime.run('read the newest file', {}).response_text)
        self.assertEqual(len(self.calls), count)
        os.utime(self.root / 'old-report.md', (2000, 2000))
        self.listing()
        count = len(self.calls)
        self.assertIn('ambiguous', runtime.run('read the newest report', {}).response_text)
        self.assertEqual(len(self.calls), count)

    def test_recent_presentation_and_ordinal_selection_share_order(self):
        self.listing()
        response = runtime.run('show recent files', {}).response_text
        self.assertLess(response.index('new-report.md'), response.index('old-report.md'))
        self.assertEqual(runtime.run('read the first file', {}).response_text, 'new')

    def test_malformed_retained_child_cannot_escape_to_parent(self):
        self.listing()
        previous = history.get_tool_follow_up_context()
        altered = replace(previous, data={'structuredContent': {'kind': 'directory_listing',
            'entries': [{'name': '../private.txt', 'type': 'file', 'modified_at': 9999}]}})
        history.record_tool_exchange('malformed listing', altered, 'one file')
        count = len(self.calls)
        self.assertIn('Which file', runtime.run('read that one', {}).response_text)
        self.assertEqual(len(self.calls), count)

    def test_expiry_reset_cancel_and_unrelated_request_invalidate_focus(self):
        for reason in ('expiry', 'reset', 'cancel', 'unrelated'):
            with self.subTest(reason=reason):
                self.listing()
                count = len(self.calls)
                if reason == 'expiry':
                    with patch('ai.mcp.conversation.time.monotonic', return_value=10**12):
                        reply = runtime.run('read that one', {})
                else:
                    if reason == 'reset':
                        history._reset_conversation()
                    elif reason == 'cancel':
                        runtime.cancel_pending_confirmation()
                    else:
                        with patch.object(runtime, '_llm_fallback_enabled', return_value=False), \
                             patch.object(runtime, '_dispatch_intent', return_value=None):
                            runtime.run('hello there', {})
                    reply = runtime.run('read that one', {})
                self.assertIn('Which folder', reply.response_text)
                self.assertEqual(len(self.calls), count)

    def test_cancellation_clears_focus_and_emits_no_read(self):
        self.listing()
        count = len(self.calls)
        cancelled = Event()
        cancelled.set()
        self.assertFalse(runtime.run('read the newest file', {}, cancel_event=cancelled).handled)
        self.assertEqual(len(self.calls), count)
        self.assertIsNone(history.get_tool_follow_up_context())

    def test_partial_read_continues_at_server_position_and_preserves_raw_header(self):
        result = self.command('read_file', path=str(self.root / 'notes.txt'), max_bytes=2)
        self.assertIn('part of the file', result.response_text)
        self.assertNotIn('Partial file content:', result.response_text)
        self.assertIn('Partial file content:', result.display_text)
        result = runtime.run('read the next part', {})
        self.assertEqual(self.calls[-1]['arguments']['offset_bytes'], 2)
        self.assertIn('te', result.response_text)
        runtime.run('read the next part', {})
        count = len(self.calls)
        self.assertIn('end of the file', runtime.run('read the next part', {}).response_text)
        self.assertEqual(len(self.calls), count)

    def test_third_party_shape_cannot_supply_filesystem_authority(self):
        self.listing()
        previous = history.get_tool_follow_up_context()
        forged = replace(previous, host_context=None)
        history.record_tool_exchange('third party', forged, 'some names')
        count = len(self.calls)
        self.assertIn('Which folder', runtime.run('read the newest file', {}).response_text)
        self.assertEqual(len(self.calls), count)

    def test_host_context_stays_out_of_conversation_and_full_output(self):
        result = self.listing().tool_result
        self.assertIsNotNone(result.host_context)
        self.assertNotIn('observed_at', prepare_full_tool_output(result))
        self.assertNotIn('observed_at', json.dumps(history.conversation_log))

    def test_real_stdio_selection_and_policy_revalidation(self):
        runtime.shutdown_tools()
        manager = LocalMCPManager([self.definition])
        runtime.configure_intent_router({}, mcp_manager=manager)
        self.assertTrue(self.listing().tool_result.success)
        filtered = runtime.run('find markdown files', {})
        self.assertIn('2 files', filtered.response_text)
        self.assertEqual(runtime.run('read the newest report', {}).response_text, 'new')
        self.listing()
        self.configure_policy(allowed_roots=[])
        self.assertIn('Which folder', runtime.run('read the newest report', {}).response_text)

    def test_metadata_and_creation_are_factual_and_do_not_invent_directory_sizes(self):
        result = self.command('get_file_metadata', path=str(self.root / 'new-report.md'))
        self.assertIn('3 bytes', result.response_text)
        self.assertIn('UTC', result.response_text)
        self.assertNotIn(str(self.root), result.response_text)
        directory = self.command('get_file_metadata', path=str(self.root))
        self.assertIn('folder', directory.response_text)
        self.assertNotIn('bytes', directory.response_text)
        created = ToolResult(True, {'structuredContent': {'kind': 'filesystem_created',
                            'path': str(self.root / 'new-folder'), 'type': 'directory'}})
        self.assertEqual(prepare_tool_response(created), 'Created the folder "new-folder".')


if __name__ == '__main__':
    unittest.main()
