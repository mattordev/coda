"""Reader grants, bounded arguments, and live policy revocation without public traffic."""

import importlib.util
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from utils.public_http import HTTPResult, NetworkError
from ai.mcp.config import discover_definitions
from ai.mcp.transport import SDKStdioTransport


class WebReadMCPTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'integrations/coda-webread/server.py'
        spec = importlib.util.spec_from_file_location('webread_test', path)
        self.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.reader)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.reader.CONFIG_PATH = Path(temporary.name) / 'reader.json'
        self.configure()
        self.observed = HTTPResult('https://example.com/', 'https://example.com/article', 200,
                                   (('Content-Type', 'text/html'),), b'<main>A source</main>', False, 50, 1)

    def configure(self, **changes):
        config = {'network': {'enabled': True, 'allowed_hosts': ['example.com']},
                  'extraction': {'max_chars': 100, 'max_links': 2}}
        config.update(changes)
        self.reader.CONFIG_PATH.write_text(json.dumps(config), encoding='utf-8')

    def code(self, result):
        self.assertTrue(result.is_error)
        return result.structured_content['error']['code']

    def test_missing_malformed_and_disabled_configs_never_fetch(self):
        for raw, expected in [(None, 'configuration_error'), ('{', 'configuration_error'),
                              ('{"network":{"enabled":true,"enabled":false}}', 'configuration_error'),
                              ('{"network":{"timeout_seconds":NaN}}', 'configuration_error'),
                              ('{}', 'network_disabled')]:
            with self.subTest(raw=raw), patch.object(self.reader, 'get') as fetch:
                if raw is None:
                    self.reader.CONFIG_PATH.unlink()
                else:
                    self.reader.CONFIG_PATH.write_text(raw, encoding='utf-8')
                self.assertEqual(self.code(self.reader.read_url('https://example.com')), expected)
                fetch.assert_not_called()

    def test_arguments_can_only_narrow_operator_limits(self):
        for args in ({'max_chars': 101}, {'max_chars': True}, {'max_links': 3}, {'max_links': -1}):
            with self.subTest(args=args), patch.object(self.reader, 'get') as fetch:
                self.assertEqual(self.code(self.reader.read_url('https://example.com', **args)), 'invalid_arguments')
                fetch.assert_not_called()

    def test_success_is_structured_fetched_text_and_final_source(self):
        with patch.object(self.reader, 'get', return_value=self.observed) as fetch:
            result = self.reader.read_url('https://example.com/', max_chars=5, max_links=0)
        page = result.structured_content
        self.assertFalse(result.is_error)
        self.assertEqual(page['text'], 'A sou')
        self.assertEqual(page['url'], self.observed.final_url)
        self.assertTrue(page['partial'])
        self.assertIn('Partial webpage text', result.content[0].text)
        self.assertEqual(fetch.call_args.args[1].allowed_hosts, ('example.com',))
        self.assertTrue(fetch.call_args.kwargs['validate']())

    def test_revocation_discards_finished_response(self):
        def fetch(*_args, validate):
            self.assertTrue(validate())
            self.configure(network={'enabled': False})
            self.assertFalse(validate())
            return self.observed
        with patch.object(self.reader, 'get', side_effect=fetch):
            result = self.reader.read_url('https://example.com')
        self.assertEqual(self.code(result), 'network_denied')
        self.assertNotIn('A source', str(result))

    def test_typed_transport_failure_is_preserved_without_url_secrets(self):
        with patch.object(self.reader, 'get', side_effect=NetworkError('network_denied', 'The destination is not allowed.')):
            result = self.reader.read_url('https://secret:password@example.com/')
        self.assertEqual(self.code(result), 'network_denied')
        self.assertNotIn('password', str(result))

    def test_unknown_fields_oversize_and_wrong_types_fail_closed(self):
        configs = [{'network': {'unknown': True}}, {'network': {'enabled': 'true'}},
                   {'network': {'allowed_hosts': '*'}}, {'extraction': {'max_chars': False}},
                   {'unexpected': 1}, {'network': {'timeout_seconds': 31}}]
        for config in configs:
            with self.subTest(config=config):
                self.reader.CONFIG_PATH.write_text(json.dumps(config), encoding='utf-8')
                self.assertEqual(self.code(self.reader.read_url('https://example.com')), 'configuration_error')
        self.reader.CONFIG_PATH.write_bytes(b' ' * 65537)
        self.assertEqual(self.code(self.reader.read_url('https://example.com')), 'configuration_error')

    def test_actual_sdk_discovery_and_denial_in_both_session_modes(self):
        directory = self.reader.CONFIG_PATH.parent / 'definitions'
        directory.mkdir()
        (directory / 'reader.json').write_text(json.dumps({
            'id': 'reader', 'bundled_server': 'webread', 'trusted': True, 'enabled': True,
            'settings': {'config_file': str(self.reader.CONFIG_PATH)}, 'allowed_tools': ['read_url'],
        }), encoding='utf-8')
        definition = discover_definitions(directory).servers[0]
        self.assertIsNone(definition.bundle_error)
        transport = SDKStdioTransport()
        for mode in ('operation', 'persistent'):
            with self.subTest(mode=mode):
                current = replace(definition, session_mode=mode)
                try:
                    found = transport.request(current, 'discover')
                    self.assertTrue(found.success, found.error)
                    tool = found.data['tools'][0]
                    self.assertEqual(tool['name'], 'read_url')
                    self.assertTrue(tool['annotations']['readOnlyHint'])
                    denied = transport.request(current, 'invoke', tool_name='read_url',
                                               arguments={'url': 'http://127.0.0.1/private'})
                    self.assertFalse(denied.success)
                    self.assertEqual(denied.data['structuredContent']['error']['code'], 'network_denied')
                finally:
                    self.assertTrue(transport.retire('reader').success)


if __name__ == '__main__':
    unittest.main()
