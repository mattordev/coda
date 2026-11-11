"""Web-search failure contracts use a mocked DDGS boundary, never live searches."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

from ai.tools.models import ToolError, ToolResult
from ai.tools.presentation import prepare_tool_display, prepare_tool_response


class DDGSException(Exception):
    """Test double for the optional provider dependency."""


class TimeoutException(DDGSException):
    """Test double preserving the provider's typed timeout hierarchy."""


class WebSearchMCPTests(unittest.TestCase):
    def setUp(self):
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
        guard = patch.object(self.server, 'load_config', return_value={'search': {'max_results': 5, 'timeout': 10}})
        guard.start()
        self.addCleanup(guard.stop)

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
        result = self.normalized(self.server.search_web(' '))
        self.assertEqual(result.data['structuredContent']['error']['code'], 'invalid_arguments')
        self.client.text.assert_not_called()
