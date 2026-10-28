"""Catalog/security tests use disposable bundles, never operator configuration."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai.mcp import bundled
from ai.mcp.catalog import MCPRuntimeCatalog
from ai.mcp.config import discover_definitions
from ai.mcp.runtime import LocalMCPManager
from ai.mcp.transport import SDKStdioTransport
from ai.intents.models import Intent, IntentRequest
from ai.tools.models import ToolResult


class BundleCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'integrations' / 'fixture'
        self.directory.mkdir(parents=True)
        self.configs = self.root / 'mcp_servers'
        self.configs.mkdir()
        self.manifest = {'id': 'fixture', 'version': '1.0.0', 'entrypoint': 'server.py',
                         'settings_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
        self.write_manifest()
        (self.directory / 'server.py').write_text('raise AssertionError("must not import")', encoding='utf-8')
        root_patch = patch.object(bundled, 'INSTALL_ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def write_manifest(self, manifest=None, directory=None):
        ((directory or self.directory) / 'manifest.json').write_text(
            json.dumps(self.manifest if manifest is None else manifest), encoding='utf-8')

    def definition(self, **changes):
        (self.configs / 'fixture.json').write_text(json.dumps(
            {'id': 'instance', 'bundled_server': 'fixture', **changes}), encoding='utf-8')
        return discover_definitions(self.configs)

    def test_data_only_reads_and_independent_instance_identity(self):
        with patch('subprocess.Popen') as launch, patch('importlib.import_module') as importing:
            found = self.definition()
            manager = LocalMCPManager(found.servers)
            self.assertEqual(bundled.discover_bundles().errors, {})
        launch.assert_not_called()
        importing.assert_not_called()
        self.assertFalse(found.servers[0].enabled)
        self.assertEqual(manager.inspect()['instance'], {
            'bundle': 'fixture', 'enabled': False, 'trusted': False,
            'availability': 'available', 'error': None, 'connection': 'configured'})

    def test_disabled_and_untrusted_cannot_reach_any_launch_path(self):
        for options in ({}, {'enabled': False, 'trusted': True}, {'enabled': True, 'trusted': False}):
            with self.subTest(options=options):
                found = self.definition(session_mode='persistent', startup_mode='eager', **options)
                transport = Mock()
                manager = LocalMCPManager(found.servers, transport=transport)
                self.assertFalse(manager.discover('instance').success)
                self.assertEqual(manager.start_eager(), {})
                catalog = MCPRuntimeCatalog(manager)
                self.assertEqual(catalog.discover(), ())
                self.assertEqual(catalog.discover(server_id='instance'), ())
                with patch('subprocess.Popen') as launch:
                    self.assertFalse(SDKStdioTransport().request(found.servers[0], 'discover').success)
                launch.assert_not_called()
                transport.request.assert_not_called()

    def test_trusted_empty_allowlist_discovers_but_cannot_invoke(self):
        transport = Mock()
        transport.request.return_value = ToolResult(success=True, data={'tools': [
            {'name': 'echo', 'inputSchema': {'type': 'object', 'properties': {}}}]})
        found = self.definition(enabled=True, trusted=True)
        manager = LocalMCPManager(found.servers, transport=transport)
        self.assertTrue(manager.discover('instance').success)
        executor = manager.executor('mcp.instance.echo')
        request = IntentRequest(Intent('mcp.instance.echo', 'Echo'), 'echo', 1.0, arguments={})
        self.assertEqual(executor.execute(request).error.code, 'permission_denied')
        self.assertEqual(transport.request.call_count, 1)

    def test_invalid_schema_manifest_and_settings_fail_independently(self):
        cases = [{'unknown': True}, {'settings_schema': {'type': 'not-a-type'}},
                 {'settings_schema': {'$ref': 'https://example.test/schema'}},
                 {'defaults': {'trusted': True}}, {'entrypoint': '../outside.py'},
                 {'dependencies': ['not.a.top.level']}, {'version': 1}]
        for change in cases:
            with self.subTest(change=change):
                self.write_manifest(self.manifest | change)
                result = self.definition(enabled=True, trusted=True)
                self.assertTrue(result.errors)
                if result.servers:
                    transport = Mock()
                    self.assertFalse(LocalMCPManager(result.servers, transport=transport).discover('instance').success)
                    transport.request.assert_not_called()
        self.write_manifest()
        for settings in ({'unknown': True}, [], None):
            with self.subTest(settings=settings):
                self.assertTrue(self.definition(settings=settings).errors)

    def test_unsafe_entrypoints_native_paths_and_junctions(self):
        for entrypoint in ('../outside.py', '/absolute.py', 'C:/escape.py', '//host/share.py',
                           'C:relative.py', 'sub\\server.py', './server.py', 'sub/../server.py'):
            with self.subTest(entrypoint=entrypoint):
                self.write_manifest(self.manifest | {'entrypoint': entrypoint})
                self.assertIsNotNone(bundled.discover_bundles().bundles['fixture'].error)

    def test_duplicates_have_no_winner_even_if_first_manifest_invalid(self):
        other = self.root / 'integrations' / 'other'
        other.mkdir()
        self.write_manifest(directory=other)
        self.assertNotIn('fixture', bundled.discover_bundles().bundles)
        self.write_manifest(self.manifest | {'version': False})
        self.assertNotIn('fixture', bundled.discover_bundles().bundles)

    def test_duplicate_json_keys_and_valid_neighbors(self):
        (self.directory / 'manifest.json').write_text('{"id":"fixture","id":"fixture"}', encoding='utf-8')
        good = self.root / 'integrations' / 'good'
        good.mkdir()
        (good / 'server.py').touch()
        self.write_manifest(self.manifest | {'id': 'good'}, good)
        catalog = bundled.discover_bundles()
        self.assertIn('fixture', catalog.errors)
        self.assertIn('good', catalog.bundles)

    def test_settings_defaults_never_supply_security_and_overrides_are_strict(self):
        self.manifest['settings_schema']['properties'] = {
            'label': {'type': 'string', 'security': False},
            'allowed_roots': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}}
        self.manifest['defaults'] = {'label': 'default'}
        self.write_manifest()
        result = self.definition(settings={'label': 'custom', 'allowed_roots': ['/tmp']})
        self.assertEqual(result.errors, {})
        self.assertEqual(json.loads(result.servers[0].environment()['CODA_MCP_SETTINGS'])['label'], 'custom')
        self.assertTrue(self.definition(settings={'label': 1}).errors)
        self.manifest['defaults']['allowed_roots'] = ['/']
        self.write_manifest()
        self.assertNotIn('fixture', bundled.discover_bundles().bundles)

    def test_missing_dependency_and_interpreter_are_safe_inspectable_states(self):
        self.write_manifest(self.manifest | {'dependencies': ['coda_missing_fixture_module']})
        found = self.definition(enabled=True, trusted=True)
        status = LocalMCPManager(found.servers).inspect()['instance']
        self.assertEqual(status['availability'], 'error')
        self.assertIn('locked requirements', status['error'])
        self.write_manifest()
        with patch.object(bundled.sys, 'executable', str(self.root / 'missing-python')):
            self.assertIn('Interpreter', bundled.discover_bundles().bundles['fixture'].error)

    def test_current_interpreter_with_empty_path(self):
        with patch.dict('os.environ', {'PATH': ''}):
            found = self.definition(enabled=True)
        self.assertEqual(found.servers[0].command, sys.executable)

    def test_bad_bundle_does_not_reserve_an_instance_id_over_valid_neighbor(self):
        self.definition(settings={'unknown': True})
        (self.configs / 'later.json').write_text(json.dumps({
            'id': 'instance', 'command': sys.executable, 'args': ['legacy.py']
        }), encoding='utf-8')
        found = discover_definitions(self.configs)
        self.assertIn('fixture.json', found.errors)
        self.assertNotIn('later.json', found.errors)
        self.assertEqual(found.servers[0].args, ('legacy.py',))

    def test_permission_file_requires_explicit_nonempty_root_configuration(self):
        permissions = self.root / 'permissions.json'
        self.manifest['settings_schema']['properties']['config_file'] = {'type': 'string'}
        self.manifest['setting_arguments'] = {'config_file': '--config'}
        self.manifest['configuration_files'] = [{
            'setting': 'config_file', 'default': 'permissions.json',
            'schema': {'type': 'object', 'required': ['allowed_roots'], 'properties': {
                'allowed_roots': {'type': 'array', 'minItems': 1,
                                  'items': {'type': 'string', 'pattern': '^(/|[A-Za-z]:[\\\\/])'}}}}
        }]
        self.write_manifest()
        for data in ({}, {'allowed_roots': []}, {'allowed_roots': ['relative']}, {'allowed_roots': [None]}):
            with self.subTest(data=data):
                permissions.write_text(json.dumps(data), encoding='utf-8')
                self.assertTrue(self.definition(settings={'config_file': str(permissions)}).errors)
        permissions.write_text(json.dumps({'allowed_roots': [str(self.root)]}), encoding='utf-8')
        self.assertEqual(self.definition(settings={'config_file': str(permissions)}).errors, {})

    def test_linked_bundle_rejected(self):
        linked = self.root / 'integrations' / 'linked'
        try:
            linked.symlink_to(self.directory, target_is_directory=True)
        except OSError:
            self.skipTest('Symbolic links require OS privileges')
        self.assertIn('linked', bundled.discover_bundles().errors)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction behavior')
    def test_windows_junction_bundle_rejected(self):
        junction = self.root / 'integrations' / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(self.directory)],
                                capture_output=True, check=False)
        if result.returncode:
            self.skipTest('Junction creation unavailable')
        try:
            self.assertIn('junction', bundled.discover_bundles().errors)
        finally:
            junction.rmdir()
