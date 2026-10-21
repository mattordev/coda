"""Diagnostics are retained locally with redaction and bounded rotation."""

import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai.tools import diagnostics


class ToolDiagnosticsTests(unittest.TestCase):
    def test_local_log_retains_reason_and_redacts_secrets(self):
        logger = logging.Logger("isolated-tool-test")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mcp" / "failures.log"
            with patch.object(diagnostics, "_LOGGER", logger), patch.object(diagnostics, "LOG_PATH", path):
                try:
                    diagnostics.record_tool_failure("mcp.test.tool", "PermissionError: " + "sk-" + "a" * 48)
                    text = path.read_text(encoding="utf-8")
                    self.assertIn("PermissionError", text)
                    self.assertNotIn("sk-", text)
                    self.assertEqual(logger.handlers[0].backupCount, 2)
                finally:
                    for handler in logger.handlers:
                        handler.close()

    def test_unwritable_log_does_not_break_result_handling(self):
        logger = logging.Logger("isolated-tool-test")
        with patch.object(diagnostics, "_LOGGER", logger), patch.object(Path, "mkdir", side_effect=OSError):
            diagnostics.record_tool_failure("mcp.test.tool", "The operation timed out.")
