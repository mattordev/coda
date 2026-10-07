"""Tests for the shared tool execution result contract."""

import unittest

from ai.tools.models import ToolError, ToolResult


class ToolErrorTests(unittest.TestCase):
    def test_rejects_non_string_error_fields(self):
        for field in ("code", "message"):
            for value in (12, False, [], {}):
                with self.subTest(field=field, value=value):
                    values = {"code": "device_error", "message": "Cannot print."}
                    values[field] = value

                    with self.assertRaisesRegex(ValueError, "must be strings"):
                        ToolError(**values)

    def test_valid_error_preserves_code_and_message(self):
        error = ToolError(code="device_error", message="Cannot print.")

        self.assertEqual(error.code, "device_error")
        self.assertEqual(error.message, "Cannot print.")

    def test_rejects_none_in_either_field(self):
        for field in ("code", "message"):
            with self.subTest(field=field):
                values = {"code": "device_error", "message": "Cannot print."}
                values[field] = None

                with self.assertRaises(ValueError):
                    ToolError(**values)

    def test_rejects_empty_or_whitespace_only_strings_in_either_field(self):
        for field in ("code", "message"):
            for blank in ("", " ", "   ", "\t", "\n", " \t\n "):
                with self.subTest(field=field, blank=repr(blank)):
                    values = {"code": "device_error", "message": "Cannot print."}
                    values[field] = blank

                    with self.assertRaises(ValueError):
                        ToolError(**values)


class ToolResultTests(unittest.TestCase):
    def test_success_requires_a_boolean(self):
        for value in (None, 0, 1, "false", []):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must be a boolean"):
                    ToolResult(success=value)

    def test_error_requires_a_structured_tool_error(self):
        for value in ("Cannot print.", {"code": "device_error"}, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must be a ToolError"):
                    ToolResult(success=False, error=value)

    def test_success_can_have_no_payload(self):
        result = ToolResult(success=True)

        self.assertTrue(result.success)
        self.assertIsNone(result.data)
        self.assertIsNone(result.error)

    def test_success_preserves_tool_specific_data(self):
        payload = {"duration_seconds": 120, "filament_used_grams": 8.5}

        result = ToolResult(success=True, data=payload)

        self.assertIs(result.data, payload)

    def test_failure_can_have_no_error_for_native_compatibility(self):
        result = ToolResult(success=False)

        self.assertFalse(result.success)
        self.assertIsNone(result.error)

    def test_failure_preserves_error_and_partial_data(self):
        error = ToolError(
            code="device_error",
            message="The printer stopped before completing the job.",
        )
        payload = {"completed_layers": 12}

        result = ToolResult(success=False, data=payload, error=error)

        self.assertFalse(result.success)
        self.assertIs(result.error, error)
        self.assertIs(result.data, payload)

    def test_success_cannot_contain_an_error(self):
        error = ToolError(code="device_error", message="Cannot print.")

        with self.assertRaisesRegex(ValueError, "successful tool result"):
            ToolResult(success=True, error=error)


if __name__ == "__main__":
    unittest.main()
