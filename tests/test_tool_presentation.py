"""Speech feedback must contain useful output, never the MCP result envelope."""

from copy import deepcopy
import unittest

from ai.tools.models import ToolError, ToolResult
from ai.tools.presentation import prepare_tool_response


class ToolPresentationTests(unittest.TestCase):
    def test_echo_json_text_becomes_just_the_message_without_mutating_result(self):
        data = {
            "_meta": {"server": "my-first-server"},
            "content": [{"type": "text", "text": '{"message":"Hello from CODA"}'}],
            "structuredContent": {"message": "Hello from CODA"},
            "isError": False, "resultType": "complete",
        }
        original = deepcopy(data)
        self.assertEqual(prepare_tool_response(ToolResult(True, data)), "Hello from CODA")
        self.assertEqual(data, original)

    def test_server_narrative_is_preferred_to_machine_fields(self):
        result = ToolResult(True, {
            "content": [{"type":"text", "text":"The printer is ready."}],
            "structuredContent": {"state": "ready", "code": 200},
        })
        self.assertEqual(prepare_tool_response(result), "The printer is ready.")

    def test_structured_only_output_uses_readable_labels(self):
        result = ToolResult(True, {"structuredContent":{"state":"ready", "temperature_c":21}})
        self.assertEqual(prepare_tool_response(result), "State: ready; Temperature c: 21")

    def test_multiple_text_blocks_and_scalar_wrappers_are_supported(self):
        result = ToolResult(True, {"content":[
            {"type":"text", "text":"First line"},
            {"type":"image", "data":"do-not-read", "mimeType":"image/png"},
            {"type":"text", "text":'{"result":"Second line"}'},
        ]})
        self.assertEqual(prepare_tool_response(result), "First line\nSecond line")

    def test_empty_and_non_text_output_get_safe_completion_feedback(self):
        for data in (None, {}, {"_meta":{"internal":"hidden"}}, {"content":[{"type":"image","data":"hidden"}]}):
            with self.subTest(data=data):
                self.assertEqual(prepare_tool_response(ToolResult(True, data)), "Tool completed.")

    def test_secrets_are_sanitized_before_truncation(self):
        secret = "sk-" + "a" * 48
        result = ToolResult(True, {"content":[{"type":"text", "text":secret + " " + "x" * 100} ]})
        text = prepare_tool_response(result, max_chars=30)
        self.assertNotIn(secret[:10], text)
        self.assertLessEqual(len(text), 30)
        self.assertTrue(text.endswith("…"))

    def test_errors_use_safe_message_instead_of_failure_payload(self):
        result = ToolResult(False, {"private":"do-not-read"}, ToolError("timeout","The operation timed out."))
        self.assertEqual(prepare_tool_response(result), "The operation timed out.")

    def test_invalid_limits_are_rejected(self):
        for limit in (True, 0, -1, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                prepare_tool_response(ToolResult(True), max_chars=limit)
