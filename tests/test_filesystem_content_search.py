"""Literal content search has independent traversal, I/O and response budgets."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from ai.intents import IntentRequest
from ai.mcp.config import LocalServerDefinition
from ai.mcp.runtime import LocalMCPManager
from ai.tools.interpretation import interpret_result
from ai.tools.models import ToolResult
from ai.tools.presentation import prepare_tool_display

SERVER = Path(__file__).resolve().parents[1] / 'integrations/coda-filesystem/server.py'
SPEC = importlib.util.spec_from_file_location('content_search_filesystem', SERVER)
filesystem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filesystem)


class FilesystemContentSearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'allowed'
        self.root.mkdir()
        self.config = self.base / 'permissions.json'
        self.configure()
        guard = patch.object(filesystem, 'CONFIG_PATH', self.config)
        guard.start()
        self.addCleanup(guard.stop)

    def configure(self, **changes):
        self.config.write_text(json.dumps({'read': {'allowed_roots': [str(self.root)],
            'blocked_paths': ['**/.env', '**/.ssh'], **changes}}), encoding='utf-8')

    def search(self, query='needle', **options):
        return filesystem.search_content(str(self.root), query, **options)

    def data(self, result):
        self.assertFalse(result.is_error, result)
        return result.structured_content

    def test_literal_matching_returns_real_path_line_column_and_bounded_snippet(self):
        source = self.root / 'report.md'
        source.write_bytes('first\r\nCAFÉ needle.* here\r\nneedle.* twice needle.*\r\n'.encode('utf-8'))
        data = self.data(self.search('needle.*'))
        self.assertFalse(data['partial'])
        self.assertEqual([(match['line'], match['column']) for match in data['matches']], [(2, 6), (3, 1)])
        self.assertTrue(all(match['path'] == str(source) for match in data['matches']))
        self.assertEqual(data['bytes_read'], source.stat().st_size)
        self.assertEqual(self.data(self.search('needle.+'))['matches'], [])

    def test_case_sensitivity_and_utf8_bom_are_explicit(self):
        (self.root / 'notes.txt').write_text('CAFÉ\nNeedle', encoding='utf-8-sig')
        self.assertEqual(len(self.data(self.search('café'))['matches']), 1)
        self.assertEqual(self.data(self.search('café', case_sensitive=True))['matches'], [])
        self.assertEqual(self.data(self.search('Needle', case_sensitive=True))['matches'][0]['line'], 2)

    def test_empty_tree_has_complete_no_match_result(self):
        data = self.data(self.search())
        self.assertEqual(data['matches'], [])
        self.assertEqual(data['files_examined'], 0)
        self.assertFalse(data['partial'])

    def test_binary_encoding_and_large_file_skips_make_no_match_incomplete(self):
        (self.root / 'binary').write_bytes(b'needle\0binary')
        (self.root / 'latin1').write_bytes(b'\xffneedle')
        (self.root / 'large').write_bytes(b'x' * 65537)
        data = self.data(self.search())
        self.assertEqual(data['matches'], [])
        self.assertTrue(data['partial'])
        self.assertEqual(set(data['limits_reached']), {'binary_file', 'invalid_encoding', 'file_byte_limit'})
        self.assertEqual(data['bytes_read'], 20)

    def test_blocked_and_outside_paths_never_have_contents_opened(self):
        (self.root / '.env').write_text('needle', encoding='utf-8')
        private = self.root / '.ssh'
        private.mkdir()
        (private / 'notes').write_text('needle', encoding='utf-8')
        (self.root / 'public').write_text('needle', encoding='utf-8')
        original = filesystem._content_bytes
        with patch.object(filesystem, '_content_bytes', wraps=original) as reading:
            data = self.data(self.search())
        self.assertEqual([call.args[0].name for call in reading.call_args_list], ['public'])
        self.assertEqual(len(data['matches']), 1)
        outside = filesystem.search_content(str(self.base), 'needle')
        self.assertTrue(outside.is_error)
        self.assertEqual(outside.structured_content['error']['code'], 'outside_allowed_roots')

    def test_links_are_skipped_even_when_their_targets_are_allowed(self):
        target = self.root / 'actual.txt'
        target.write_text('needle', encoding='utf-8')
        try:
            (self.root / 'alias.txt').symlink_to(target)
        except OSError:
            self.skipTest('Native symbolic links unavailable')
        data = self.data(self.search())
        self.assertEqual([match['name'] for match in data['matches']], ['actual.txt'])

    def test_file_result_entry_and_total_byte_limits_disclose_incompleteness(self):
        for index in range(4):
            (self.root / f'{index}.txt').write_text('needle\nneedle', encoding='utf-8')
        for options, reason in (({'max_files': 1}, 'file_limit'), ({'max_results': 1}, 'result_limit'),
                                ({'max_entries': 1}, 'traversal_entry_limit'),
                                ({'max_total_bytes': 13}, 'total_byte_limit')):
            with self.subTest(options=options):
                data = self.data(self.search(**options))
                self.assertTrue(data['partial'])
                self.assertIn(reason, data['limits_reached'])
                self.assertLessEqual(data['bytes_read'], options.get('max_total_bytes', 1048576) + 1)

    def test_long_lines_have_bounded_snippets_and_correct_original_columns(self):
        (self.root / 'long.txt').write_text('x' * 1000 + 'needle' + 'y' * 1000, encoding='utf-8')
        match = self.data(self.search())['matches'][0]
        self.assertEqual(match['column'], 1001)
        self.assertEqual(match['snippet_start_column'], 921)
        self.assertEqual(len(match['snippet']), 240)
        self.assertTrue(match['snippet_truncated'])
        self.assertIn('needle', match['snippet'])

    def test_output_and_deadline_caps_do_not_claim_complete_scans(self):
        (self.root / 'long.txt').write_text('needle' + 'x' * 1000, encoding='utf-8')
        with patch.object(filesystem, 'SEARCH_OUTPUT_CHARS', 350):
            data = self.data(self.search())
        self.assertIn('output_limit', data['limits_reached'])
        with patch.object(filesystem, 'SEARCH_SECONDS', 0):
            data = self.data(self.search())
        self.assertTrue(any('time_limit' in reason for reason in data['limits_reached']))

    def test_policy_revocation_during_read_discards_all_collected_matches(self):
        (self.root / 'notes').write_text('needle', encoding='utf-8')
        original = filesystem._content_bytes

        def revoke(*args):
            data = original(*args)
            self.configure(allowed_roots=[])
            return data

        with patch.object(filesystem, '_content_bytes', side_effect=revoke):
            result = self.search()
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content['error']['code'], 'permission_denied')
        self.assertNotIn('matches', result.structured_content)

    def test_malformed_limits_queries_and_globs_are_rejected(self):
        for options in ({'query': ''}, {'query': '\nneedle'}, {'query': 'x' * 257}, {'query': '\ud800'},
                        {'max_files': True}, {'max_files': 201}, {'max_results': 0},
                        {'max_file_bytes': 1048577}, {'max_total_bytes': 8388609},
                        {'case_sensitive': 'false'}, {'max_depth': -1}, {'pattern': '../*'}):
            with self.subTest(options=options):
                values = {'query': 'needle', **options}
                result = self.search(**values)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content['error']['code'], 'invalid_arguments')

    def test_concurrent_searches_do_not_share_matches_or_counters(self):
        (self.root / 'notes').write_text('alpha\nbeta', encoding='utf-8')
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(self.search, ('alpha', 'beta')))
        self.assertEqual([self.data(result)['matches'][0]['line'] for result in results], [1, 2])
        self.assertEqual([self.data(result)['files_examined'] for result in results], [1, 1])

    def test_speech_keeps_scope_and_details_without_a_second_model_call(self):
        (self.root / 'notes').write_text('needle\nneedle', encoding='utf-8')
        raw = self.search(max_results=1)
        result = ToolResult(True, raw.model_dump(mode='json', by_alias=True))
        with patch('ai.llm_router.core.get_provider_order', side_effect=AssertionError('No model needed')):
            spoken = interpret_result(result, 'Find this phrase')
        self.assertIn('1 matching line', spoken.text)
        self.assertIn('incomplete', spoken.text)
        self.assertNotIn('needle\nneedle', spoken.text)
        self.assertIn('needle', prepare_tool_display(result))

    def test_real_stdio_permissions_schema_cancellation_and_revocation(self):
        (self.root / 'notes.md').write_text('needle', encoding='utf-8')
        for mode in ('operation', 'persistent'):
            with self.subTest(mode=mode):
                self.configure()
                definition = LocalServerDefinition('content', sys.executable,
                    args=(str(SERVER), '--config', str(self.config)), trusted=True,
                    allowed_tools=frozenset(), session_mode=mode)
                manager = LocalMCPManager([definition])
                try:
                    found = manager.discover('content')
                    self.assertTrue(found.success, found.error)
                    intent = next(intent for intent in found.data if intent.name == 'mcp.content.search_content')
                    request = IntentRequest(intent, 'Search text', 1, arguments={'path': str(self.root), 'query': 'needle'})
                    self.assertEqual(manager.executor(intent.name).execute(request).error.code, 'permission_denied')
                    manager.replace_definition(replace(definition, allowed_tools=frozenset({'search_content'})))
                    found = manager.discover('content')
                    request = replace(request, intent=next(intent for intent in found.data if intent.name == request.intent.name))
                    result = manager.executor(intent.name).execute(request)
                    self.assertTrue(result.success, result.error)
                    self.assertEqual(result.data['structuredContent']['matches'][0]['line'], 1)
                    invalid = replace(request, arguments={**request.arguments, 'max_files': True})
                    self.assertEqual(manager.executor(intent.name).execute(invalid).error.code, 'invalid_arguments')
                    cancelled = Event()
                    cancelled.set()
                    self.assertEqual(manager.executor(intent.name).execute(request, cancel_event=cancelled).error.code, 'cancelled')
                    self.configure(allowed_roots=[])
                    self.assertFalse(manager.executor(intent.name).execute(request).success)
                finally:
                    self.assertTrue(manager.close()['content'].success)

    def test_active_content_read_is_cancelled_or_timed_out_and_server_reaped(self):
        import psutil
        (self.root / 'notes').write_text('needle', encoding='utf-8')
        wrapper = self.base / 'paused-server.py'
        wrapper.write_text('''
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('paused_content_fixture', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.CONFIG_PATH = Path(sys.argv[2])
original = module._content_bytes
def paused(*args):
    marker = Path(sys.argv[3])
    pending = marker.with_suffix('.pending')
    pending.write_text(str(os.getpid()), encoding='utf-8')
    pending.replace(marker)
    time.sleep(60)
    return original(*args)
module._content_bytes = paused
module.server.run(transport='stdio')
''', encoding='utf-8')
        for action in ('cancel', 'timeout'):
            with self.subTest(action=action):
                marker = self.base / (action + '-started')
                definition = LocalServerDefinition('active', sys.executable,
                    args=(str(wrapper), str(SERVER), str(self.config), str(marker)), trusted=True,
                    allowed_tools=frozenset({'search_content'}), session_mode='persistent',
                    startup_timeout_seconds=15, timeout_seconds=10 if action == 'cancel' else 1)
                manager = LocalMCPManager([definition])
                cancelled = Event()
                worker = None
                results = []
                try:
                    found = manager.discover('active')
                    self.assertTrue(found.success, found.error)
                    intent = next(intent for intent in found.data if intent.name == 'mcp.active.search_content')
                    request = IntentRequest(intent, 'Search text', 1, arguments={'path': str(self.root), 'query': 'needle'})
                    worker = Thread(target=lambda: results.append(manager.executor(intent.name).execute(request, cancel_event=cancelled)), daemon=True)
                    worker.start()
                    deadline = time.monotonic() + 8
                    while not marker.exists() and time.monotonic() < deadline:
                        cancelled.wait(0.01)
                    self.assertTrue(marker.exists(), 'Content reading must start before interruption.')
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
                finally:
                    cancelled.set()
                    manager.close()
                    if worker is not None:
                        worker.join(12)


if __name__ == '__main__':
    unittest.main()
