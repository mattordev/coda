"""Provider boundaries are mocked: these tests never make live searches."""

import importlib.util
import io
import json
import os
from http.client import BadStatusLine
from pathlib import Path
import sys
from types import ModuleType
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

from ai.tools.models import ToolError, ToolResult
from ai.tools.presentation import prepare_tool_display, prepare_tool_response


class DDGSException(Exception):
    """Test double for the optional provider dependency."""


class TimeoutException(DDGSException):
    """Test double preserving the provider's typed timeout hierarchy."""


class WebSearchMCPTests(unittest.TestCase):
    def setUp(self):
        environment_guard = patch.dict(os.environ)
        environment_guard.start()
        self.addCleanup(environment_guard.stop)
        os.environ.pop('SEARXNG_BASE_URL', None)
        dependency = ModuleType('ddgs')
        constructor = MagicMock()
        dependency.DDGS = constructor
        exceptions = ModuleType('ddgs.exceptions')
        exceptions.DDGSException = DDGSException
        exceptions.TimeoutException = TimeoutException
        path = Path(__file__).resolve().parents[1] / 'integrations/coda-websearch/server.py'
        spec = importlib.util.spec_from_file_location('coda_websearch_test', path)
        self.server = importlib.util.module_from_spec(spec)
        # Restore only these two optional modules. Restoring all of sys.modules
        # would unload SDK lazy imports while its cached class references remain.
        replacements = {'ddgs': dependency, 'ddgs.exceptions': exceptions}
        previous = {name: sys.modules.get(name) for name in replacements}
        sys.modules.update(replacements)
        try:
            spec.loader.exec_module(self.server)
        finally:
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.client = constructor.return_value.__enter__.return_value
        self.config = {'search': {'max_results': 5, 'timeout': 10}}
        self.original_load_config = self.server.load_config
        guard = patch.object(self.server, 'load_config', return_value=self.config)
        guard.start()
        self.addCleanup(guard.stop)
        http_guard = patch.object(self.server, 'urlopen')
        self.http = http_guard.start()
        self.addCleanup(http_guard.stop)

    def searxng_response(self, payload):
        self.config['search']['searxng_base_url'] = 'https://search.example.org/prefix/'
        self.http.return_value.__enter__.return_value = io.StringIO(json.dumps(payload))

    def normalized(self, response):
        return ToolResult(not response.is_error, response.model_dump(mode='json', by_alias=True),
                          ToolError('mcp_tool_error', 'The MCP tool reported a failure.') if response.is_error else None)

    def test_success_preserves_text_and_structured_results_and_calls_once(self):
        self.client.text.return_value = [{'title': 'Bed adhesion', 'href': 'https://example.com', 'body': 'Clean the bed.'}]
        response = self.server.search_web('bed adhesion', max_results=2)
        self.assertFalse(response.is_error)
        self.assertEqual(response.structured_content['results'][0]['url'], 'https://example.com')
        self.assertIn('Clean the bed.', prepare_tool_response(self.normalized(response)))
        self.client.text.assert_called_once_with('bed adhesion', max_results=2)

    def test_empty_response_acknowledges_no_results_without_claiming_no_information_exists(self):
        self.client.text.return_value = []
        result = self.normalized(self.server.search_web('bed adhesion'))
        self.assertFalse(result.success)
        self.assertEqual(result.data['structuredContent']['error']['code'], 'no_results')
        self.assertEqual(prepare_tool_response(result), "Sorry, I couldn't get any results for that search.")
        self.client.text.assert_called_once()

    def test_base_exception_is_unavailable_even_when_it_says_no_results(self):
        for message in ('No results found.', 'BackendFailure: private diagnostic'):
            with self.subTest(message=message):
                self.client.text.side_effect = DDGSException(message)
                self.client.text.reset_mock()
                result = self.normalized(self.server.search_web('bed adhesion'))
                self.assertEqual(result.data['structuredContent']['error']['code'], 'unavailable')
                self.assertEqual(prepare_tool_response(result), "Sorry, I couldn't get any results for that search.")
                self.assertIn('DDGSException', prepare_tool_display(result))
                self.assertIn(message, prepare_tool_display(result))
                self.client.text.assert_called_once()

    def test_typed_timeout_stays_distinct_and_never_retries(self):
        self.client.text.side_effect = TimeoutException('private provider detail')
        result = self.normalized(self.server.search_web('bed adhesion'))
        self.assertEqual(result.data['structuredContent']['error']['code'], 'timeout')
        self.assertIn('too long', prepare_tool_response(result))
        self.assertNotIn('private', prepare_tool_response(result))
        self.client.text.assert_called_once()

    def test_empty_query_never_contacts_provider(self):
        self.config['search']['searxng_base_url'] = 'https://search.example.org'
        result = self.normalized(self.server.search_web(' '))
        self.assertEqual(result.data['structuredContent']['error']['code'], 'invalid_arguments')
        self.client.text.assert_not_called()
        self.http.assert_not_called()

    def test_searxng_success_preserves_contract_encodes_query_and_never_uses_backup(self):
        self.searxng_response({'results': [
            {'title': 'Python', 'url': 'https://python.org', 'content': 'Documentation'},
            {'title': 'Extra', 'url': 'https://example.org', 'content': 'More'},
        ]})
        response = self.server.search_web('Python & C++ café', max_results=1)
        self.assertFalse(response.is_error)
        self.assertEqual(response.structured_content['provider'], 'searxng')
        self.assertEqual(response.structured_content['results'], [
            {'title': 'Python', 'url': 'https://python.org', 'snippet': 'Documentation'}])
        self.assertIn('Documentation', prepare_tool_response(self.normalized(response)))
        self.http.assert_called_once()
        request = self.http.call_args.args[0]
        self.assertEqual(urlsplit(request.full_url).path, '/prefix/search')
        self.assertEqual(parse_qs(urlsplit(request.full_url).query),
                         {'q': ['Python & C++ café'], 'format': ['json']})
        self.assertEqual(request.get_header('Accept'), 'application/json')
        self.assertEqual(self.http.call_args.kwargs, {'timeout': 10})
        self.client.text.assert_not_called()

    def test_primary_failures_use_backup_once(self):
        failures = [TimeoutError('private'), URLError(TimeoutError('private')),
                    URLError('connection refused'),
                    HTTPError('https://private.example', 403, 'Forbidden', {}, None),
                    ValueError('invalid JSON'), BadStatusLine('broken HTTP response')]
        for error in failures:
            with self.subTest(error=error):
                self.config['search']['searxng_base_url'] = 'https://search.example.org'
                self.http.reset_mock()
                self.http.side_effect = error
                self.client.text.reset_mock()
                self.client.text.return_value = [{'title': 'Backup', 'href': 'https://example.org', 'body': 'Snippet'}]
                response = self.server.search_web('python', max_results=2)
                self.assertFalse(response.is_error)
                self.assertEqual(response.structured_content['provider'], 'ddgs')
                self.assertEqual(response.structured_content['results'][0]['snippet'], 'Snippet')
                self.http.assert_called_once()
                self.client.text.assert_called_once_with('python', max_results=2)

    def test_empty_malformed_and_unusable_primary_responses_fall_back(self):
        for payload in ({'results': []}, {}, [], {'results': None}, {'results': {}},
                        {'results': [None, 'bad', {}, {'url': 'javascript:bad'}]}):
            with self.subTest(payload=payload):
                self.searxng_response(payload)
                self.client.text.reset_mock()
                self.client.text.return_value = [{'href': 'https://example.org'}]
                result = self.server.search_web('python')
                self.assertEqual(result.structured_content['provider'], 'ddgs')
                self.client.text.assert_called_once()

    def test_malformed_json_falls_back(self):
        self.searxng_response({})
        self.http.return_value.__enter__.return_value = io.StringIO('<html>Not JSON</html>')
        self.client.text.return_value = [{'href': 'https://example.org'}]
        self.assertEqual(self.server.search_web('python').structured_content['provider'], 'ddgs')

    def test_both_failures_retain_diagnostics_and_safe_conversational_feedback(self):
        self.config['search']['searxng_base_url'] = 'https://search.example.org'
        self.http.side_effect = TimeoutError('private primary detail')
        self.client.text.side_effect = DDGSException('private backup detail')
        response = self.server.search_web('python')
        self.assertEqual(response.structured_content['error']['code'], 'unavailable')
        self.assertEqual([(a['provider'], a['code']) for a in response.structured_content['attempts']],
                         [('searxng', 'timeout'), ('ddgs', 'unavailable')])
        result = self.normalized(response)
        self.assertNotIn('private', prepare_tool_response(result))
        self.assertIn('private backup detail', prepare_tool_display(result))
        self.http.assert_called_once()
        self.client.text.assert_called_once()

    def test_empty_backup_does_not_hide_primary_timeout_or_unavailability(self):
        for error, code in ((TimeoutError(), 'timeout'), (URLError(TimeoutError()), 'timeout'),
                            (URLError('offline'), 'unavailable')):
            with self.subTest(code=code):
                self.config['search']['searxng_base_url'] = 'https://search.example.org'
                self.http.side_effect = error
                self.client.text.return_value = []
                result = self.server.search_web('python')
                self.assertEqual(result.structured_content['error']['code'], code)

    def test_both_empty_providers_return_no_results(self):
        self.searxng_response({'results': []})
        self.client.text.return_value = []
        response = self.server.search_web('python')
        self.assertEqual(response.structured_content['error']['code'], 'no_results')
        self.assertEqual(len(response.structured_content['attempts']), 2)

    def test_limits_are_clamped_and_unusable_entries_are_skipped(self):
        for requested, expected in ((None, 5), (100, 5), (0, 1), (-2, 1), (2, 2)):
            with self.subTest(requested=requested):
                self.searxng_response({'results': [None, {}, {'url': 'file:///private'},
                    {'url': 'http://[bad'}, {'url': None}] + [
                    {'url': f'https://example.org/{i}', 'title': None, 'content': 1} for i in range(10)]})
                result = self.server.search_web('python', max_results=requested)
                self.assertEqual(len(result.structured_content['results']), expected)
                self.assertEqual(result.structured_content['results'][0]['title'], '')
                self.assertEqual(result.structured_content['results'][0]['snippet'], '')
        self.client.text.assert_not_called()

    def test_ddgs_output_is_also_bounded_and_filters_unusable_results(self):
        self.client.text.return_value = [None, {}, {'href': 'javascript:bad'}] + [
            {'href': f'https://example.org/{i}'} for i in range(10)]
        self.assertEqual(len(self.server.search_web('python', max_results=2).structured_content['results']), 2)
        self.http.assert_not_called()

    def test_configuration_errors_never_contact_either_provider(self):
        self.server.load_config.side_effect = ValueError('private config value')
        result = self.normalized(self.server.search_web('python'))
        self.assertEqual(result.data['structuredContent']['error']['code'], 'unavailable')
        self.assertNotIn('private', prepare_tool_display(result))
        self.http.assert_not_called()
        self.client.text.assert_not_called()

    def test_config_defaults_and_invalid_values(self):
        from unittest.mock import mock_open
        for config, expected in (({}, {'max_results': 5, 'timeout': 10, 'searxng_base_url': ''}),
                                 ({'search': {'searxng_base_url': 'http://localhost:8080/subpath/'}},
                                  {'max_results': 5, 'timeout': 10, 'searxng_base_url': 'http://localhost:8080/subpath/'})):
            with patch.object(Path, 'open', mock_open(read_data=json.dumps(config))):
                self.assertEqual(self.original_load_config()['search'], expected)
        for key, values in (('max_results', (0, 11, True, '5')),
                            ('timeout', (0, 31, False, None)),
                            ('searxng_base_url', (None, 'ftp://host', 'http://',
                             'https://user:password@host', 'https://host?q=x', 'https://host/#frag',
                             'https://bad host', 'http://localhost:invalid', 'http://localhost:99999'))):
            for value in values:
                with self.subTest(key=key, value=value):
                    with patch.object(Path, 'open', mock_open(read_data=json.dumps({'search': {key: value}}))):
                        with self.assertRaises(ValueError):
                            self.original_load_config()

    def test_invalid_config_shapes_are_rejected(self):
        from unittest.mock import mock_open
        for value in ([], None, {'search': None}, {'search': []}):
            with self.subTest(value=value):
                with patch.object(Path, 'open', mock_open(read_data=json.dumps(value))):
                    with self.assertRaises(ValueError):
                        self.original_load_config()

    def test_environment_url_overrides_json_and_blank_preserves_legacy_config(self):
        from unittest.mock import mock_open
        config = {'search': {'searxng_base_url': 'https://legacy.example.org'}}
        for value, expected in (('http://localhost:5080', 'http://localhost:5080'),
                                ('', 'https://legacy.example.org')):
            with self.subTest(value=value):
                with patch.dict(os.environ, {'SEARXNG_BASE_URL': value}), \
                     patch.object(Path, 'open', mock_open(read_data=json.dumps(config))):
                    self.assertEqual(self.original_load_config()['search']['searxng_base_url'], expected)

    def test_environment_url_works_without_json_url_and_is_validated(self):
        from unittest.mock import mock_open
        with patch.object(Path, 'open', mock_open(read_data='{"search": {}}')):
            with patch.dict(os.environ, {'SEARXNG_BASE_URL': 'http://localhost:5080'}):
                self.assertEqual(self.original_load_config()['search']['searxng_base_url'], 'http://localhost:5080')
            with patch.dict(os.environ, {'SEARXNG_BASE_URL': 'ftp://invalid'}):
                with self.assertRaises(ValueError):
                    self.original_load_config()

    def test_manifest_accepts_new_and_legacy_configs_and_rejects_invalid_settings(self):
        from jsonschema import Draft202012Validator
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / 'integrations/coda-websearch/manifest.json').read_text(encoding='utf-8'))
        schema = manifest['configuration_files'][0]['schema']
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        example = json.loads((root / 'integrations/coda-websearch/websearch.config.json.example').read_text(encoding='utf-8'))
        self.assertTrue(validator.is_valid(example))
        self.assertTrue(validator.is_valid({'search': {'max_results': 5, 'timeout': 10}}))
        self.assertTrue(validator.is_valid({'search': {'searxng_base_url': 'http://localhost:8080/subpath'}}))
        self.assertTrue(validator.is_valid({'search': {}}))
        for value in ({'search': {'searxng_base_url': 'ftp://host'}},
                      {'search': {'searxng_base_url': 'http://user:pass@host'}},
                      {'search': {'searxng_base_url': 'http://host/?q=x'}},
                      {'search': {'timeout': 0}}, {'search': {'max_results': 11}},
                      {'search': {'unknown': True}}):
            with self.subTest(value=value):
                self.assertFalse(validator.is_valid(value))
