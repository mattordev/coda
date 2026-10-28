"""Workspace selection must never expand filesystem permissions."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai.intents import IntentRequest
from ai.mcp.config import LocalServerDefinition
from ai.mcp.runtime import LocalMCPManager
from ai.mcp import workspace
from ai.tools.models import ToolResult


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.config = self.base / 'permissions.json'
        self.definition = LocalServerDefinition('files', sys.executable, bundled_server='filesystem',
            trusted=True, allowed_tools=frozenset({'read_file', 'list_directory'}),
            settings_json=json.dumps({'config_file': str(self.config)}))
        self.configure()

    def configure(self, **changes):
        self.read = {'allowed_roots': [str(self.root)], 'workspace_roots': [str(self.root)],
                     'blocked_paths': ['**/.env', '**/.ssh', '**/credentials.json'], **changes}
        self.config.write_text(json.dumps({'read': self.read}), encoding='utf-8')

    def resolve(self, reference, tool='read_file', **options):
        return workspace.resolve_arguments(self.definition, tool, {'path': reference}, **options)['path']

    def rejected(self, code, reference='README', tool='read_file', **options):
        with self.assertRaises(workspace.WorkspaceReferenceError) as raised:
            self.resolve(reference, tool, **options)
        self.assertEqual(raised.exception.code, code)
        return str(raised.exception)


class WorkspaceTests(WorkspaceFixture):
    def test_default_permissions_file_is_relative_to_bundle_not_process_directory(self):
        (self.root / 'README.md').touch()
        (self.base / 'filesystem.config.json').write_bytes(self.config.read_bytes())
        definition = replace(self.definition, settings_json='{}')
        catalog = SimpleNamespace(bundles={'filesystem': SimpleNamespace(root=self.base)})
        with patch.object(workspace.bundled, 'discover_bundles', return_value=catalog):
            resolved = workspace.resolve_arguments(definition, 'read_file', {'path': 'README'})
        self.assertEqual(resolved['path'], str(self.root / 'README.md'))

    def test_case_insensitive_common_extension_alias_resolves_without_mutation(self):
        target = self.root / 'README.md'
        target.touch()
        arguments = {'path': 'readme'}
        original = deepcopy(arguments)
        resolved = workspace.resolve_arguments(self.definition, 'read_file', arguments)
        self.assertEqual(resolved['path'], str(target))
        self.assertEqual(arguments, original)

    def test_relative_directory_components_and_explicit_extension(self):
        parent = self.root / 'docs'
        parent.mkdir()
        target = parent / 'Guide.MD'
        target.touch()
        for reference in ('docs/Guide.MD', 'docs\\guide.md', 'docs/guide'):
            with self.subTest(reference=reference):
                self.assertEqual(self.resolve(reference), str(target))
        self.assertEqual(self.resolve('docs', 'list_directory'), str(parent))
        self.assertEqual(self.resolve('.', 'list_directory'), str(self.root))

    def test_no_recursive_or_fuzzy_search_and_no_unknown_extension_alias(self):
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / 'README.md').touch()
        (self.root / 'version.exe').touch()
        self.rejected('workspace_not_found')
        self.rejected('workspace_not_found', 'version')
        self.assertEqual(self.resolve('version.exe'), str(self.root / 'version.exe'))
        self.rejected('workspace_not_found', 'read me')

    def test_empty_or_missing_workspace_does_not_infer_cwd_or_allowed_root(self):
        (self.root / 'README.md').touch()
        for remove in (False, True):
            self.configure(workspace_roots=[])
            if remove:
                del self.read['workspace_roots']
                self.config.write_text(json.dumps({'read': self.read}), encoding='utf-8')
            self.rejected('workspace_required')

    def test_absolute_and_nonbundled_or_unrelated_tools_are_passed_through(self):
        self.config.unlink()
        for definition, tool, reference in (
            (self.definition, 'read_file', str(self.base / 'not-existing')),
            (replace(self.definition, bundled_server=None), 'read_file', 'README'),
            (self.definition, 'delete_file', 'README'),
        ):
            arguments = {'path': reference}
            self.assertIs(workspace.resolve_arguments(definition, tool, arguments), arguments)

    def test_no_ambient_working_directory_is_used(self):
        target = self.root / 'README.md'
        target.touch()
        (self.base / 'README.md').touch()
        previous = Path.cwd()
        try:
            os.chdir(self.base)
            self.assertEqual(self.resolve('README'), str(target))
        finally:
            os.chdir(previous)

    def test_multiple_extensions_or_workspaces_require_clarification(self):
        (self.root / 'README.md').touch()
        (self.root / 'README.txt').touch()
        self.rejected('workspace_ambiguous')
        self.assertEqual(self.resolve('README.md'), str(self.root / 'README.md'))
        other = self.base / 'other'
        other.mkdir()
        (other / 'README.md').touch()
        self.configure(workspace_roots=[str(self.root), str(other)], allowed_roots=[str(self.base)])
        self.rejected('workspace_ambiguous', 'README.md')
        self.rejected('workspace_ambiguous', '.', 'list_directory')

    def test_unique_match_across_workspaces_and_overlapping_roots_is_allowed(self):
        other = self.root / 'other'
        other.mkdir()
        target = self.root / 'README.md'
        target.touch()
        self.configure(workspace_roots=[str(other), str(self.root)])
        self.assertEqual(self.resolve('readme'), str(target))
        self.configure(workspace_roots=[str(self.root), str(other)])
        (other / 'guide.md').touch()
        self.assertEqual(self.resolve('other/guide.md'), str(other / 'guide.md'))

    def test_workspace_never_grants_access_outside_allowed_roots_or_sibling_prefix(self):
        sibling = self.base / 'workspace-other'
        sibling.mkdir()
        (sibling / 'README.md').touch()
        for roots in ([str(sibling)], [str(self.base)]):
            self.configure(workspace_roots=roots)
            with patch.object(workspace.os, 'scandir') as scan:
                self.rejected('workspace_unsafe')
            scan.assert_not_called()
        self.configure(allowed_roots=[])
        self.rejected('workspace_unsafe')

    def test_blocked_names_and_ancestors_are_never_returned_or_enumerated(self):
        (self.root / '.env').touch()
        (self.root / '.ssh').mkdir()
        (self.root / '.ssh' / 'README.md').touch()
        (self.root / 'credentials.json').touch()
        for reference in ('.env', 'credentials.json'):
            self.rejected('workspace_not_found', reference)
        with patch.object(workspace.os, 'scandir') as scan:
            self.rejected('workspace_unsafe', '.ssh/README.md')
        scan.assert_not_called()

    def test_a_blocked_alias_does_not_make_an_accessible_match_ambiguous(self):
        (self.root / 'README.md').touch()
        (self.root / 'README.txt').touch()
        self.configure(blocked_paths=['**/README.txt'])
        self.assertEqual(self.resolve('readme'), str(self.root / 'README.md'))

    def test_unsafe_syntax_and_foreign_drive_forms_are_not_guessed(self):
        for reference in ('../README', 'docs/../README', './README', 'docs//README',
                          'C:README', 'C:\\README', '//server/share/README' if os.name != 'nt' else '\\README',
                          'docs/file:stream', 'README*', 'README?', '[README]', '', 'README\x00', 'README.', 'README ',
                          'NUL', 'docs/CON.txt', 'COM1', 'README\x7f', 'README\x9b', 'R' * 1025):
            if Path(reference).is_absolute():
                continue  # Absolute requests retain the independent server contract.
            with self.subTest(reference=reference):
                self.rejected('workspace_unsafe', reference)

    def test_enumeration_limit_refuses_a_result_even_if_a_match_was_seen(self):
        (self.root / 'README.md').touch()
        for name in ('a', 'b', 'c'):
            (self.root / name).touch()
        with patch.object(workspace, 'MAX_ENTRIES', 2):
            self.rejected('workspace_limit')

    def test_configuration_invalid_or_oversized_fails_closed(self):
        cases = [
            {'workspace_roots': ['relative']}, {'workspace_roots': None},
            {'workspace_roots': [str(self.root)] * 2}, {'workspace_roots': [str(self.root / str(i)) for i in range(9)]},
            {'allowed_roots': [None]}, {'blocked_paths': [None]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.configure(**changes)
                self.rejected('configuration_error')
        for raw in ('{', '{}', '{"read":null}', '{"read":{},"read":{}}', ' ' * (workspace.MAX_CONFIG_BYTES + 1)):
            with self.subTest(size=len(raw)):
                self.config.write_text(raw, encoding='utf-8')
                self.rejected('configuration_error')

    def test_unavailable_root_missing_parent_and_wrong_kind_do_not_resolve(self):
        self.configure(workspace_roots=[str(self.root / 'missing')])
        self.rejected('workspace_not_found')
        self.configure()
        (self.root / 'README.md').mkdir()
        self.rejected('workspace_not_found')
        self.rejected('workspace_not_found', 'missing/README')

    def test_policy_and_roots_are_reloaded_each_request_without_cached_resolution(self):
        target = self.root / 'README.md'
        target.touch()
        self.assertEqual(self.resolve('readme'), str(target))
        self.configure(blocked_paths=['**/README.md'])
        self.rejected('workspace_not_found')
        self.configure(workspace_roots=[])
        self.rejected('workspace_required')

    def test_policy_change_during_resolution_never_invokes_a_stale_match(self):
        (self.root / 'README.md').touch()
        read_config = workspace._configuration
        def changed(definition):
            result = read_config(definition)
            self.configure(blocked_paths=['**/README.md'])
            return result
        with patch.object(workspace, '_configuration', side_effect=changed):
            self.rejected('workspace_changed')

    def test_cancellation_before_and_during_enumeration_aborts_resolution(self):
        event = Event()
        event.set()
        self.rejected('cancelled', cancel_event=event)
        event.clear()
        (self.root / 'README.md').touch()
        def match(*_):
            event.set()
            return True
        with patch.object(workspace, '_matches', side_effect=match):
            self.rejected('cancelled', cancel_event=event)

    def make_link(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            self.skipTest(f'Native symlink creation unavailable: {error}')

    def test_symlink_files_and_directories_cannot_escape_or_bypass_blocked_names(self):
        outside = self.base / 'outside.md'
        outside.touch()
        self.make_link(self.root / 'README.md', outside)
        self.rejected('workspace_not_found')
        self.make_link(self.root / 'outside', self.base, directory=True)
        self.rejected('workspace_unsafe', 'outside/outside.md')

    def test_even_in_root_links_and_linked_workspace_roots_require_explicit_absolute_paths(self):
        target = self.root / 'target.md'
        target.touch()
        self.make_link(self.root / 'README.md', target)
        self.rejected('workspace_not_found')
        alias = self.base / 'alias'
        self.make_link(alias, self.root, directory=True)
        self.configure(workspace_roots=[str(alias)])
        self.rejected('workspace_unsafe')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction semantics')
    def test_windows_directory_junction_escape_is_denied_without_symlink_privileges(self):
        junction = self.root / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(self.base)],
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(junction.rmdir)
        self.rejected('workspace_unsafe', 'junction/README.md')

    def test_case_rules_preserve_native_root_boundaries_and_detect_name_ambiguity(self):
        target = self.root / 'README.md'
        target.touch()
        self.configure(allowed_roots=[str(self.root).upper()])
        if os.name == 'nt':
            self.assertEqual(self.resolve('readme'), str(target))
        else:
            self.rejected('workspace_unsafe')
            self.configure()
            (self.root / 'readme.md').touch()
            self.rejected('workspace_ambiguous', 'README.md')


class WorkspaceExecutionTests(WorkspaceFixture):
    def manager(self, definition=None):
        transport = Mock()
        transport.request.return_value = ToolResult(True, {'tools': [{
            'name': 'read_file', 'description': 'Read a file', 'inputSchema': {
                'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path'],
                'additionalProperties': False,
            },
        }]})
        manager = LocalMCPManager([definition or self.definition], transport=transport)
        manager.discover('files')
        transport.request.reset_mock()
        transport.request.return_value = ToolResult(True, {'content': [{'type': 'text', 'text': 'File contents.'}]})
        intent = manager._tools['mcp.files.read_file'][2]
        return manager, transport, IntentRequest(intent, 'Read the README', 1, arguments={'path': 'readme'})

    def test_execution_validates_then_resolves_then_invokes_once(self):
        (self.root / 'README.md').touch()
        manager, transport, request = self.manager()
        result = manager.executor(request.intent.name).execute(request)
        self.assertTrue(result.success)
        transport.request.assert_called_once()
        self.assertEqual(transport.request.call_args.kwargs['arguments'], {'path': str(self.root / 'README.md')})
        self.assertEqual(request.arguments, {'path': 'readme'})

    def test_ambiguity_and_schema_failure_prevent_transport_and_need_no_model(self):
        (self.root / 'README.md').touch()
        (self.root / 'README.txt').touch()
        manager, transport, request = self.manager()
        executor = manager.executor(request.intent.name)
        self.assertEqual(executor.execute(request).error.code, 'workspace_ambiguous')
        self.assertEqual(executor.execute(replace(request, arguments={'path': 5})).error.code, 'invalid_arguments')
        transport.request.assert_not_called()


    def test_permissions_and_confirmation_precede_any_workspace_enumeration(self):
        for options, expected in (({'allowed_tools': frozenset()}, 'permission_denied'),
                                  ({'confirm_tools': frozenset({'read_file'})}, 'confirmation_required')):
            with self.subTest(options=options):
                manager, transport, request = self.manager(replace(self.definition, **options))
                with patch.object(workspace, '_configuration') as config:
                    self.assertEqual(manager.executor(request.intent.name).execute(request).error.code, expected)
                config.assert_not_called()
                transport.request.assert_not_called()

    def test_confirmation_rechecks_changed_workspace_policy(self):
        (self.root / 'README.md').touch()
        manager, transport, request = self.manager(replace(self.definition, confirm_tools=frozenset({'read_file'})))
        executor = manager.executor(request.intent.name)
        self.assertEqual(executor.execute(request).error.code, 'confirmation_required')
        self.configure(blocked_paths=['**/README.md'])
        self.assertEqual(executor.execute(request, confirmed=True).error.code, 'workspace_not_found')
        transport.request.assert_not_called()

    def test_definition_change_after_permission_check_aborts_before_workspace_inspection(self):
        manager, transport, request = self.manager()
        permission = manager._permission
        def replace_after_permission(*args, **kwargs):
            result = permission(*args, **kwargs)
            self.assertTrue(manager.replace_definition(self.definition).success)
            return result
        with patch.object(manager, '_permission', side_effect=replace_after_permission), \
             patch('ai.mcp.runtime.resolve_arguments') as resolve:
            reply = manager.executor(request.intent.name).execute(request)
        self.assertEqual(reply.error.code, 'tool_not_found')
        resolve.assert_not_called()
        transport.request.assert_not_called()

    def test_natural_reference_reaches_entrypoint_and_ambiguity_is_spoken_without_invocation(self):
        from collections import deque
        from ai.intents import IntentRouter
        from ai.intents.local_classifier import LocalClassifierStrategy
        from ai.intents.router import ExactMatchStrategy, CommandPrefixStrategy
        import utils.on_command as command
        import utils.llm_service as history
        (self.root / 'README.md').touch()
        manager, transport, request = self.manager()
        tool = {'name': 'read_file', 'inputSchema': request.intent.input_schema}
        transport.request.side_effect = lambda _definition, operation, **_kwargs: (
            ToolResult(True, {'tools': [tool]}) if operation == 'discover'
            else ToolResult(True, {'content': [{'type': 'text', 'text': 'The project is CODA.'}]})
        )
        classifier = Mock(return_value=(json.dumps({'intent': request.intent.name, 'confidence': 1,
                                                     'arguments': {'path': 'readme'}}), None))
        for name in ('_mcp_catalog', '_intent_registry', '_intent_router', '_pending_confirmation'):
            guard = patch.object(command, name, None)
            guard.start()
            self.addCleanup(guard.stop)
        for name, value in (('conversation_log', []), ('_tool_results', deque(maxlen=8)), ('_tool_focus', None)):
            guard = patch.object(history, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.addCleanup(command.shutdown_tools)
        with patch.object(command, 'create_router', side_effect=lambda registry: IntentRouter(registry, strategies=(
            ExactMatchStrategy(), CommandPrefixStrategy(), LocalClassifierStrategy(classifier)))), \
             patch('ai.intents.local_classifier.logger.record_attempt'), \
             patch.object(command, 'route_request') as fallback, \
             patch.object(history, 'call_with_messages') as interpretation, patch('builtins.print') as output:
            command.configure_intent_router({}, mcp_manager=manager)
            reply = command.on_command('Please read the README', {})
            self.assertTrue(reply.tool_result.success)
            output.assert_called_once_with(f'CODA: {reply.response_text}')
            self.assertEqual(transport.request.call_args.kwargs['arguments']['path'], str(self.root / 'README.md'))
            transport.request.reset_mock()
            output.reset_mock()
            (self.root / 'README.txt').touch()
            reply = command.on_command('Please read the README', {})
            self.assertEqual(reply.tool_result.error.code, 'workspace_ambiguous')
            self.assertIn('More than one accessible match', reply.response_text)
            output.assert_called_once_with(f'CODA: {reply.response_text}')
            transport.request.assert_not_called()
            fallback.assert_not_called()
            interpretation.assert_not_called()


class WorkspaceLiveTests(WorkspaceFixture):
    def test_real_bundled_directory_resolution_and_independent_server_enforcement(self):
        from ai.mcp.bundled import bundled_launch
        from ai.mcp.transport import SDKStdioTransport
        folder = self.root / 'docs'
        folder.mkdir()
        (folder / 'guide.md').touch()
        executable, args = bundled_launch('filesystem', {'config_file': str(self.config)})
        transport = SDKStdioTransport()
        definition = replace(self.definition, command=executable, args=args,
                             allowed_tools=frozenset({'list_directory'}), startup_timeout_seconds=15)
        manager = LocalMCPManager([definition], transport=transport)
        self.addCleanup(manager.close)
        discovery = manager.discover('files')
        self.assertTrue(discovery.success, discovery.error)
        intent = next(intent for intent in discovery.data if intent.name == 'mcp.files.list_directory')
        request = IntentRequest(intent, 'List docs', 1, arguments={'path': 'docs'})
        with patch.object(transport, 'request', wraps=transport.request) as calls:
            result = manager.executor(intent.name).execute(request)
        self.assertTrue(result.success, result.error)
        self.assertEqual(calls.call_args.kwargs['arguments']['path'], str(folder))
        self.assertEqual(result.data['structuredContent']['entries'][0]['name'], 'guide.md')
        # Absolute paths retain the server's own permission enforcement.
        denied = manager.executor(intent.name).execute(replace(request, arguments={'path': str(self.base)}))
        self.assertFalse(denied.success)
        self.assertEqual(denied.data['structuredContent']['error']['code'], 'outside_allowed_roots')



if __name__ == '__main__':
    unittest.main()
