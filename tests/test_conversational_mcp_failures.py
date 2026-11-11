"""Failure wording is deterministic; technical data stays diagnostic-only."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from ai.tools import error_feedback, interpretation
from ai.tools.models import ToolError, ToolResult
from ai.tools.presentation import prepare_tool_response


class ConversationalMCPFailureTests(unittest.TestCase):
    def test_normalized_categories_never_repeat_error_messages(self):
        for code, category, wording in (
            ('connection_error', 'unavailable', 'unavailable'),
            ('server_disabled', 'unavailable', 'unavailable'),
            ('timeout', 'timeout', 'too long'),
            ('file_not_found', 'not_found', 'file'),
            ('permission_denied', 'permission_denied', 'permission'),
            ('invalid_arguments', 'invalid_arguments', 'details'),
            ('cancelled', 'cancelled', 'cancelled'),
            ('execution_error', 'unknown_error', 'something went wrong'),
        ):
            with self.subTest(code=code):
                result = ToolResult(False, error=ToolError(code, 'DDGSException: private detail'))
                self.assertEqual(error_feedback.failure_category(result), category)
                text = prepare_tool_response(result, question='Read that file')
                self.assertIn(wording, text)
                self.assertNotIn('DDGSException', text)
                self.assertNotIn('private detail', text)

    def test_structured_claims_are_allowlisted_without_repeating_server_prose(self):
        for code, expected in (('no_results', 'no_results'), ('timeout', 'timeout'),
                               ('not_found', 'not_found'), ('new_vendor_error', 'unknown_error')):
            with self.subTest(code=code):
                data = {'structuredContent': {'error': {'code': code, 'message': 'InternalException: secret'}},
                        'content': [{'type': 'text', 'text': 'The printer is offline.'}]}
                original = deepcopy(data)
                result = ToolResult(False, data, ToolError('mcp_tool_error', 'MCP failure'))
                self.assertEqual(error_feedback.failure_category(result), expected)
                self.assertNotIn('InternalException', prepare_tool_response(result))
                self.assertNotIn('printer', prepare_tool_response(result))
                self.assertEqual(result.data, original)

    def test_legacy_ddgs_text_is_unknown_instead_of_quoted_or_guessed(self):
        result = ToolResult(False, {'content': [{'type': 'text', 'text':
            'Web search failed: DDGSException: No results found.'}]}, ToolError('mcp_tool_error', 'MCP failure'))
        self.assertEqual(error_feedback.failure_category(result), 'unknown_error')
        with patch('utils.llm_service.call_with_messages') as model:
            reply = interpretation.interpret_result(result, 'Search the web for bed adhesion')
        model.assert_not_called()
        self.assertFalse(reply.used_llm)
        self.assertNotIn('DDGSException', reply.text)
        self.assertIn('something went wrong', reply.text)

    def test_structured_search_failure_uses_truthful_request_specific_wording(self):
        for code in ('no_results', 'unavailable'):
            result = ToolResult(False, {'structuredContent': {'kind': 'web_search', 'error': {'code': code}}},
                                ToolError('mcp_tool_error', 'MCP failure'))
            self.assertEqual(prepare_tool_response(result), "Sorry, I couldn't get any results for that search.")

    def test_normalized_timeout_cannot_be_overridden_by_server_claim(self):
        result = ToolResult(False, {'structuredContent': {'error': {'code': 'permission_denied'}}},
                            ToolError('timeout', 'private'))
        self.assertEqual(error_feedback.failure_category(result), 'timeout')

    def test_malformed_unknown_and_missing_errors_still_acknowledge_failure(self):
        for result in (ToolResult(False), ToolResult(False, {'structuredContent': {'error': {'code': []}}},
                                                       ToolError('mcp_tool_error', 'private'))):
            self.assertEqual(error_feedback.failure_category(result), 'unknown_error')
            self.assertIn('something went wrong', prepare_tool_response(result))

    def test_workspace_failures_preserve_actionable_clarification_without_raw_paths(self):
        for code, wording in (
            ('workspace_ambiguous', 'more specific'), ('workspace_required', 'absolute path'),
            ('workspace_not_found', 'more specific'), ('workspace_unsafe', 'authorized'),
            ('workspace_limit', 'absolute path'), ('workspace_changed', 'again'),
            ('workspace_unavailable', 'settings'),
        ):
            with self.subTest(code=code):
                result = ToolResult(False, error=ToolError(code, 'InternalException: C:/private/location'))
                reply = prepare_tool_response(result)
                self.assertNotEqual(error_feedback.failure_category(result), 'unknown_error')
                self.assertIn(wording, reply)
                self.assertNotIn('InternalException', reply)
                self.assertNotIn('C:/private', reply)
