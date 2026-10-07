"""Tool payload privacy is checked before producing bounded conversation text."""

import json
import os
import unittest
from unittest.mock import patch

from ai.privacy.sanitizer import sanitize_structure
from ai.tools.context import prepare_tool_context
from ai.tools.models import ToolError, ToolResult


class ToolContextTests(unittest.TestCase):
    def test_nested_sanitization_reuses_existing_text_rules_without_mutating_data(self):
        original = {"values": [{"email": "person@example.com"}, "sk-" + "a" * 24]}

        sanitized = sanitize_structure(original)

        self.assertEqual(sanitized["values"][0]["email"], "[email redacted]")
        self.assertEqual(sanitized["values"][1], "[api key redacted]")
        self.assertEqual(original["values"][0]["email"], "person@example.com")

    def test_sensitive_object_keys_are_sanitized(self):
        self.assertEqual(sanitize_structure({"person@example.com": True}), {"[email redacted]": True})

    def test_cloud_policy_blocks_sensitive_full_payload_even_beyond_context_limit(self):
        result = ToolResult(success=True, data={"prefix": "x" * 5000, "password": "private-value"})

        with patch.dict(os.environ, {"CODA_PRIVACY_MODE": "strict"}):
            self.assertIsNone(prepare_tool_context(result, for_cloud=True, max_chars=20))

    def test_cloud_sanitization_is_nested_and_preserves_full_result(self):
        result = ToolResult(success=True, data={"nested": [{"key": "sk-" + "a" * 24}]})
        with patch.dict(os.environ, {
            "CODA_PRIVACY_MODE": "permissive",
            "CODA_HIGH_RISK_CLOUD_FALLBACK": "sanitize",
            "CODA_CLOUD_PRIVACY_ACTION": "sanitize",
        }):
            text = prepare_tool_context(result, for_cloud=True)

        self.assertNotIn("sk-", text)
        self.assertIn("[api key redacted]", text)
        self.assertTrue(result.data["nested"][0]["key"].startswith("sk-"))

    def test_local_context_preserves_local_privacy_distinction_and_is_bounded(self):
        result = ToolResult(success=True, data={"key": "sk-" + "a" * 24})
        with patch.dict(os.environ, {"CODA_PRIVACY_MODE": "strict"}):
            text = prepare_tool_context(result, for_cloud=False)

        self.assertIn("sk-", text)
        self.assertEqual(len(prepare_tool_context(result, for_cloud=False, max_chars=10)), 10)

    def test_summary_action_omits_payload_and_error_detail(self):
        result = ToolResult(
            success=False, data={"email": "person@example.com"},
            error=ToolError("device_error", "Contact person@example.com"),
        )
        with patch.dict(os.environ, {
            "CODA_PRIVACY_MODE": "permissive",
            "CODA_CLOUD_PRIVACY_ACTION": "summarize",
            "CODA_HIGH_RISK_CLOUD_FALLBACK": "summarize",
        }):
            self.assertEqual(prepare_tool_context(result, for_cloud=True), "The tool failed.")

    def test_non_json_payload_is_not_repr_exposed(self):
        self.assertIsNone(prepare_tool_context(ToolResult(True, data=object()), for_cloud=True))

    def test_low_risk_context_retains_normal_values(self):
        text = prepare_tool_context(ToolResult(True, data={"state": "idle"}), for_cloud=True)
        self.assertEqual(json.loads(text)["data"], {"state": "idle"})

    def test_context_limit_must_be_positive_integer(self):
        for limit in (0, -1, True, "10"):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    prepare_tool_context(ToolResult(True), for_cloud=True, max_chars=limit)


if __name__ == "__main__":
    unittest.main()
