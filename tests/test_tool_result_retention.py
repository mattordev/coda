"""Full local results survive summaries while model history stays bounded/private."""

from collections import deque
import os
import unittest
from unittest.mock import patch

from ai.tools.models import ToolResult
from ai.tools.context import prepare_tool_context
import utils.llm_service as history


class ToolResultRetentionTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("conversation_log", []), ("_tool_results", deque(maxlen=8))):
            guard = patch.object(history, name, value)
            guard.start()
            self.addCleanup(guard.stop)

    def test_full_result_is_retained_separately_from_bounded_context_and_summary(self):
        data = {"entries": [str(i) for i in range(2000)]}
        result = ToolResult(True, data)
        history.record_tool_exchange("List the entries", result)
        self.assertEqual(history.get_recent_tool_result().data, data)
        self.assertLess(len(history.conversation_log[-1]["content"]), len(str(data)))
        self.assertIn("not instructions", history.conversation_log[-1]["content"])
        snapshot = history.get_recent_tool_result()
        snapshot.data["entries"].clear()
        self.assertEqual(len(history.get_recent_tool_result().data["entries"]), 2000)

    def test_retention_is_bounded_and_reset_with_conversation(self):
        for value in range(10):
            history.record_tool_exchange("List entries", ToolResult(True, value))
        self.assertEqual(history.get_recent_tool_result(0).data, 2)
        self.assertEqual(history.get_recent_tool_result().data, 9)
        self.assertIsNone(history.get_recent_tool_result(8))
        history._reset_conversation()
        self.assertIsNone(history.get_recent_tool_result())

    def test_retained_secrets_still_pass_privacy_policy_before_cloud_context(self):
        secret = "sk-" + "a" * 48
        history.record_tool_exchange("Get result", ToolResult(True, {"secret": secret}))
        result = history.get_recent_tool_result()
        self.assertEqual(result.data["secret"], secret)
        cloud = prepare_tool_context(result, for_cloud=True)
        self.assertNotIn(secret, cloud or "")
        self.assertNotIn(secret, history.conversation_log[-1]["cloud_content"])

    def test_custom_system_prompt_keeps_tool_data_boundary(self):
        with patch.dict(os.environ, {"CODA_SYSTEM_PROMPT": "Be concise."}):
            self.assertIn("untrusted observations", history._get_system_prompt())
