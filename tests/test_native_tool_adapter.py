"""Verify native commands retain their interface through the adapter."""

import unittest
from unittest.mock import Mock

from ai.intents.models import Intent, IntentRequest
from ai.tools.models import ToolResult
from ai.tools.native import NativeCommandAdapter


class NativeCommandAdapterTests(unittest.TestCase):
    def setUp(self):
        self.request = IntentRequest(
            intent=Intent(name="maps", description="Open a map."),
            message="find the station",
            confidence=1.0,
        )

    def test_passes_original_request_to_command_once(self):
        command = Mock()
        command.run.return_value = True

        result = NativeCommandAdapter(command).execute(self.request)

        command.run.assert_called_once_with(self.request)
        self.assertIs(command.run.call_args.args[0], self.request)
        self.assertEqual(result, ToolResult(success=True))

    def test_wraps_native_false_without_inventing_error_details(self):
        command = Mock()
        command.run.return_value = False

        result = NativeCommandAdapter(command).execute(self.request)

        self.assertEqual(result, ToolResult(success=False))
        command.run.assert_called_once_with(self.request)

    def test_preserves_existing_boolean_conversion(self):
        for value, expected in ((None, False), (0, False), (1, True)):
            with self.subTest(value=value):
                command = Mock()
                command.run.return_value = value

                result = NativeCommandAdapter(command).execute(self.request)

                self.assertIs(result.success, expected)

    def test_exception_becomes_safe_failure_without_retrying_command(self):
        command = Mock()
        command.run.side_effect = RuntimeError("private-token-123")

        result = NativeCommandAdapter(command).execute(self.request)

        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertEqual(result.error.code, "execution_error")
        self.assertEqual(
            result.error.message, "The native command could not complete."
        )
        self.assertNotIn("private-token-123", repr(result))
        command.run.assert_called_once_with(self.request)

    def test_keyboard_interrupt_propagates(self):
        command = Mock()
        command.run.side_effect = KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            NativeCommandAdapter(command).execute(self.request)


if __name__ == "__main__":
    unittest.main()
