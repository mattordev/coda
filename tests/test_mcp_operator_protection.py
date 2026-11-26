"""Broad data authority must never become authority to rewrite operator policy."""

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai.intents import IntentRequest
from ai.mcp.config import discover_definitions, LocalServerDefinition
from ai.mcp.protection import check_mutation, guarded_launch, operator_paths, protected_target
from ai.mcp.runtime import LocalMCPManager

SERVER = Path(__file__).resolve().parents[1] / 'integrations/coda-filesystem/server.py'
SPEC = importlib.util.spec_from_file_location('protected_filesystem', SERVER)
filesystem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filesystem)


class OperatorProtectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.registry = self.base / 'operator' / 'definitions'
        self.registry.mkdir(parents=True)
        self.data = self.base / 'data'
        self.data.mkdir()
        self.source = self.data / 'notes.txt'
        self.source.write_bytes(b'original')
        self.policy = self.base / 'filesystem-policy.json'
        self.other_policy = self.base / 'external-policy.json'
        policy = {'read': {'allowed_roots': [str(self.base)], 'blocked_paths': []},
                  'write': {'enabled': True, 'allowed_roots': [str(self.base)]},
                  'destructive': {'enabled': True, 'allowed_roots': [str(self.base)]}}
        self.policy.write_text(json.dumps(policy), encoding='utf-8')
        self.other_policy.write_text(json.dumps(policy), encoding='utf-8')
        self.tools = ['create_file', 'create_directory', 'edit_file', 'append_file',
                      'copy_path', 'move_path', 'delete_path', 'read_file']
        for identity, permissions in (('files', self.policy), ('other', self.other_policy)):
            (self.registry / (identity + '.json')).write_text(json.dumps({
                'id': identity, 'bundled_server': 'filesystem', 'trusted': True,
                'enabled': identity == 'files', 'allowed_tools': self.tools,
                'settings': {'config_file': str(permissions)}, 'session_mode': 'persistent',
            }), encoding='utf-8')
        discovery = discover_definitions(self.registry)
        self.assertEqual(discovery.errors, {})
        self.definitions = discovery.servers
        self.paths, self.encoded = operator_paths(self.definitions)

    def manager(self, mode='persistent'):
        manager = LocalMCPManager([replace(definition, session_mode=mode) for definition in self.definitions])
        self.addCleanup(manager.close)
        found = manager.discover('files')
        self.assertTrue(found.success, found.error)
        return manager, {intent.name.rsplit('.', 1)[1]: intent for intent in found.data}

    def actions(self, target):
        return [('create_file', {'path': str(target), 'content': 'new'}),
                ('create_directory', {'path': str(target)}),
                ('edit_file', {'path': str(target), 'old_text': 'original', 'new_text': 'new', 'expected_sha256': '0' * 64}),
                ('append_file', {'path': str(target), 'content': 'new', 'expected_sha256': '0' * 64}),
                ('copy_path', {'source': str(self.source), 'destination': str(target)}),
                ('move_path', {'source': str(self.source), 'destination': str(target)}),
                ('delete_path', {'path': str(target), 'recursive': True}),
                ('move_path', {'source': str(target), 'destination': str(self.data / 'moved')})]

    def test_registry_provenance_and_disabled_bundle_configuration_are_reserved(self):
        for definition in self.definitions:
            self.assertEqual(Path(definition.definition_file).parent, self.registry)
        self.assertIn(self.registry, self.paths)
        self.assertIn(self.other_policy, self.paths)
        self.assertTrue(protected_target(self.registry / 'new-untrusted.json', self.paths))
        self.assertTrue(protected_target(self.registry.parent, self.paths, contains=True))
        self.assertFalse(protected_target(self.source, self.paths))
        self.assertFalse(check_mutation('copy_path', {'source': str(self.other_policy),
            'destination': str(self.data / 'policy-copy')}, self.paths))

    def test_invalid_bundle_configuration_still_retains_its_protected_setting(self):
        self.other_policy.write_bytes(b'invalid configuration')
        discovery = discover_definitions(self.registry)
        self.assertIn('other.json', discovery.errors)
        paths, _ = operator_paths(discovery.servers)
        self.assertIn(self.other_policy, paths)

    def test_invalid_disabled_bundle_setting_does_not_break_independent_filesystem(self):
        path = self.registry / 'other.json'
        definition = json.loads(path.read_text(encoding='utf-8'))
        definition['settings']['config_file'] = {'not': 'a path'}
        path.write_text(json.dumps(definition), encoding='utf-8')
        discovery = discover_definitions(self.registry)
        self.assertIn('other.json', discovery.errors)
        paths, _ = operator_paths(discovery.servers)
        self.assertIn(self.registry, paths)
        manager = LocalMCPManager(discovery.servers)
        self.addCleanup(manager.close)
        self.assertTrue(manager.discover('files').success)

    def test_changed_protection_set_cannot_reuse_a_session_with_stale_exclusions(self):
        manager, _ = self.manager()
        other = next(definition for definition in self.definitions if definition.name == 'other')
        new_policy = self.base / 'new-operator-policy.json'
        new_policy.write_bytes(self.other_policy.read_bytes())
        result = manager.replace_definition(replace(other, settings_json=json.dumps({'config_file': str(new_policy)})))
        self.assertTrue(result.success, result)
        # A direct protocol request bypasses host permission checks. The SDK
        # must refuse to reuse a persistent process with the old launch guard.
        result = manager._request('files', 'invoke', tool_name='create_file',
            arguments={'path': str(self.data / 'new.txt'), 'content': 'new'})
        self.assertFalse(result.success, result)
        self.assertEqual(result.error.code, 'configuration_error')
        self.assertFalse((self.data / 'new.txt').exists())
        self.assertTrue(manager.retire('files').success)
        self.assertTrue(manager.discover('files').success)
        result = manager._request('files', 'invoke', tool_name='append_file', arguments={
            'path': str(new_policy), 'content': 'new', 'expected_sha256': '0' * 64})
        self.assertFalse(result.success, result)
        self.assertEqual(result.data['structuredContent']['error']['code'], 'protected_path')

    def test_all_mutators_refuse_operator_paths_even_before_confirmation(self):
        manager, intents = self.manager()
        for target in (self.registry / 'files.json', self.registry / 'future.json', self.other_policy):
            before = target.read_bytes() if target.exists() else None
            for name, arguments in self.actions(target):
                with self.subTest(tool=name, target=target):
                    request = IntentRequest(intents[name], 'Attempt policy mutation', 1, arguments=arguments)
                    result = manager.executor(intents[name].name).execute(request)
                    self.assertFalse(result.success, result)
                    self.assertEqual(result.error.code, 'protected_path', result)
                    result = manager.executor(intents[name].name).execute(request, confirmed=True)
                    self.assertFalse(result.success, result)
                    self.assertEqual(result.error.code, 'protected_path', result)
            self.assertEqual(target.read_bytes() if target.exists() else None, before)
        self.assertEqual(self.source.read_bytes(), b'original')
        self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_real_server_refuses_policy_mutation_when_host_permission_check_is_bypassed(self):
        # Call the SDK boundary directly so this cannot pass merely because the
        # host rejected the request. The real child must use launch exclusions.
        manager, _ = self.manager(mode='operation')
        for target in (self.registry / 'future.json', self.other_policy):
            before = target.read_bytes() if target.exists() else None
            for name, arguments in self.actions(target):
                with self.subTest(tool=name, target=target):
                    result = manager._request('files', 'invoke', tool_name=name, arguments=arguments)
                    self.assertFalse(result.success, result)
                    self.assertEqual(result.data['structuredContent']['error']['code'], 'protected_path', result)
            self.assertEqual(target.read_bytes() if target.exists() else None, before)
        self.assertTrue(self.source.exists())
        self.assertEqual(list(self.base.rglob('.coda-lock')), [])

    def test_deleting_or_moving_an_ancestor_cannot_remove_operator_definitions(self):
        manager, intents = self.manager()
        for name, arguments in (('delete_path', {'path': str(self.registry.parent), 'recursive': True}),
                                ('move_path', {'source': str(self.registry.parent), 'destination': str(self.data / 'operator')})):
            result = manager.executor(intents[name].name).execute(
                IntentRequest(intents[name], 'Remove ancestor', 1, arguments=arguments), confirmed=True)
            self.assertEqual(result.error.code, 'protected_path', result)
        self.assertTrue((self.registry / 'files.json').is_file())

    def test_hardlink_alias_to_external_policy_is_protected_at_host_and_server(self):
        alias = self.data / 'innocent.txt'
        try:
            os.link(self.other_policy, alias)
        except OSError:
            self.skipTest('Native hard links unavailable')
        self.assertTrue(protected_target(alias, self.paths))
        guard = patch.object(filesystem, 'OPERATOR_PATHS', self.paths)
        with guard, patch.object(filesystem, 'CONFIG_PATH', self.policy):
            result = filesystem.create_file(str(alias), 'replacement')
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content['error']['code'], 'protected_path')
        self.assertEqual(alias.read_bytes(), self.other_policy.read_bytes())

    def test_normal_data_mutations_and_authorised_policy_reads_still_work(self):
        manager, intents = self.manager()
        destination = self.data / 'new.txt'
        request = IntentRequest(intents['create_file'], 'Create data', 1,
                                arguments={'path': str(destination), 'content': 'new'})
        result = manager.executor(intents['create_file'].name).execute(request)
        self.assertEqual(result.error.code, 'confirmation_required')
        result = manager.executor(intents['create_file'].name).execute(request, confirmed=True)
        self.assertTrue(result.success, result)
        self.assertEqual(destination.read_bytes(), b'new')
        result = manager.executor(intents['read_file'].name).execute(IntentRequest(intents['read_file'],
            'Read operator policy under explicit read authority', 1, arguments={'path': str(self.other_policy)}))
        self.assertTrue(result.success, result)

    def test_renamed_filesystem_instance_names_creation_target_before_approval(self):
        from contextlib import ExitStack
        import utils.on_command as runtime
        import utils.llm_service as history
        manager, _ = self.manager()
        with ExitStack() as stack:
            for name in ('_mcp_catalog', '_pending_confirmation', '_intent_registry', '_intent_router'):
                stack.enter_context(patch.object(runtime, name, None))
            stack.enter_context(patch.object(runtime, 'create_router', side_effect=lambda registry: runtime.IntentRouter(
                registry, strategies=(runtime.ExactMatchStrategy(), runtime.CommandPrefixStrategy()))))
            history._reset_conversation()
            runtime.configure_intent_router({}, mcp_manager=manager)
            try:
                for name, target, extra in (('create_file', self.data / 'new-file', {'content': 'new'}),
                                             ('create_directory', self.data / 'new-folder', {})):
                    result = runtime.run('mcp.files.' + name + ' ' + json.dumps({'path': str(target), **extra}), {})
                    self.assertIn(str(target), result.response_text)
                    self.assertIn('Create the', result.response_text)
                    self.assertFalse(target.exists())
                    runtime.cancel_pending_confirmation()
            finally:
                runtime.shutdown_tools()
                history._reset_conversation()
    def test_launch_metadata_is_not_tool_input_and_is_bounded(self):
        definition = self.definitions[0]
        guarded = guarded_launch(definition, self.definitions)
        self.assertEqual(guarded.args[-2:], ('--operator-paths-json', self.encoded))
        self.assertEqual(guarded.allowed_tools, definition.allowed_tools)
        self.assertNotIn('--operator-paths-json', definition.args)
        with patch.dict(os.environ, {'CODA_MCP_CONFIG_DIR': '/unused'}):
            definitions = [LocalServerDefinition('s' + str(index), sys.executable,
                           definition_file=str(self.base / ('op' + str(index)) / 'server.json')) for index in range(129)]
            with self.assertRaises(ValueError):
                operator_paths(definitions)

    def test_invalid_launch_protection_returns_configuration_failure_without_transport(self):
        from unittest.mock import Mock
        transport = Mock()
        manager = LocalMCPManager(self.definitions, transport=transport)
        self.addCleanup(manager.close)
        with patch('ai.mcp.protection.operator_paths', side_effect=ValueError('Invalid or oversized metadata')):
            result = manager.discover('files')
        self.assertFalse(result.success, result)
        self.assertEqual(result.error.code, 'configuration_error')
        transport.request.assert_not_called()

    def test_malformed_internal_launch_metadata_fails_before_server_starts(self):
        completed = subprocess.run([sys.executable, str(SERVER), '--config', str(self.policy),
            '--operator-paths-json', '{"not":"paths"}'], capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn('Invalid operator protection paths', completed.stderr)

    def test_recovery_namespace_is_reserved_case_insensitively(self):
        with patch.object(filesystem, 'CONFIG_PATH', self.policy):
            for name in ('.CODA-LOCK', '.CODA-COPY-' + 'A' * 32, '.CODA-EDIT-' + 'B' * 32 + '.TMP'):
                result = filesystem.create_directory(str(self.data / name))
                self.assertTrue(result.is_error, result)
                self.assertEqual(result.structured_content['error']['code'], 'protected_path')


if __name__ == '__main__':
    unittest.main()
