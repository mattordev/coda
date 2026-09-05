import unittest
from types import SimpleNamespace
from unittest.mock import patch

from commands import status as status_command


class StatusCommandTests(unittest.TestCase):
    def test_chooses_requested_report_type(self):
        cases = (
            ("show system status", "system"),
            ("show coda status", "coda"),
            ("show program status", "coda"),
            ("what is the status", None),
        )

        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    status_command.choose_report_type(message),
                    expected,
                )

    def test_formats_uptime_as_readable_duration(self):
        cases = (
            (0, "0 seconds"),
            (1, "1 second"),
            (90, "1 minute and 30 seconds"),
            (3720, "1 hour and 2 minutes"),
            (90061, "1 day, 1 hour, 1 minute and 1 second"),
        )

        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(
                    status_command.format_uptime(seconds),
                    expected,
                )

    @patch("commands.status.psutil.virtual_memory")
    def test_collects_system_ram_usage(self, virtual_memory):
        virtual_memory.return_value = SimpleNamespace(
            total=16 * (1024 ** 3),
            available=6 * (1024 ** 3),
            percent=62.5,
        )

        result = status_command.get_system_ram_usage()

        self.assertEqual(
            result,
            {
                "total_gb": 16.0,
                "used_gb": 10.0,
                "percent": 62.5,
            },
        )

    @patch("commands.status.psutil.sensors_battery", return_value=None)
    def test_reports_when_battery_is_unavailable(self, _sensors_battery):
        result = status_command.get_battery_status()

        self.assertFalse(result["available"])
        self.assertIn("No battery", result["reason"])

    @patch("commands.status.psutil.sensors_battery")
    def test_collects_battery_status(self, sensors_battery):
        sensors_battery.return_value = SimpleNamespace(
            percent=59.0,
            secsleft=3600,
            power_plugged=False,
        )

        result = status_command.get_battery_status()

        self.assertEqual(
            result,
            {
                "available": True,
                "percent": 59.0,
                "seconds_left": 3600,
                "plugged_in": False,
            },
        )

    @patch("commands.status.psutil.boot_time", return_value=6280)
    @patch("commands.status.time.time", return_value=10000)
    def test_formats_system_uptime(self, _current_time, _boot_time):
        self.assertEqual(
            status_command.get_system_uptime(),
            "1 hour and 2 minutes",
        )

    def test_collects_program_status_without_speaking(self):
        with (
            patch.object(status_command, "get_program_version", return_value="1.3.3"),
            patch.object(status_command, "get_used_memory", return_value=100.0),
            patch.object(status_command, "get_uptime", return_value="1 minute"),
            patch.object(status_command, "is_debug_enabled", return_value=False),
            patch.object(status_command, "get_input_mode", return_value="manual"),
            patch.object(
                status_command,
                "get_stt_status",
                return_value="Configured",
            ),
            patch.object(
                status_command.speak,
                "is_tts_available",
                return_value=True,
            ) as is_tts_available,
            patch.object(status_command.speak, "speak_response") as speak_response,
        ):
            result = status_command.get_program_status_data()

        self.assertTrue(result["tts_available"])
        is_tts_available.assert_called_once_with()
        speak_response.assert_not_called()

    @patch("commands.status.speak.speak_response")
    @patch("builtins.print")
    def test_program_status_speaks_debug_state_as_word(
        self,
        print_output,
        speak_response,
    ):
        status = {
            "version": "1.4.0",
            "memory_mb": 100.0,
            "uptime": "1 minute",
            "debug": False,
            "input_mode": "manual",
            "stt_provider": "Configured",
            "tts_available": True,
        }

        status_command.print_program_status(status)

        print_output.assert_any_call("Debug mode is currently OFF.")
        speak_response.assert_any_call("Debug mode is currently off.")

    @patch("commands.status.speak.speak_response")
    @patch("builtins.print")
    def test_reports_battery_time_remaining(self, print_output, speak_response):
        system_status = self._system_status(
            {
                "available": True,
                "percent": 59,
                "seconds_left": 3600,
                "plugged_in": False,
            }
        )

        status_command.print_system_status(system_status)

        print_output.assert_any_call(
            "Battery status is: 59%, not charging, 1 hour remaining"
        )
        spoken_summary = speak_response.call_args.args[0]
        self.assertIn(
            "with approximately 1 hour remaining",
            spoken_summary,
        )

    @patch("commands.status.speak.speak_response")
    @patch("builtins.print")
    def test_omits_unknown_battery_time(self, print_output, speak_response):
        system_status = self._system_status(
            {
                "available": True,
                "percent": 59,
                "seconds_left": status_command.psutil.POWER_TIME_UNKNOWN,
                "plugged_in": False,
            }
        )

        status_command.print_system_status(system_status)

        print_output.assert_any_call("Battery status is: 59%, not charging")
        spoken_summary = speak_response.call_args.args[0]
        self.assertNotIn("approximately", spoken_summary)

    @staticmethod
    def _system_status(battery):
        return {
            "operating_system": "Test OS",
            "processor": "Test CPU",
            "architecture": "64bit",
            "executable_format": "Test executable",
            "machine": "Test machine",
            "ram_usage": {
                "total_gb": 16.0,
                "used_gb": 8.0,
                "percent": 50.0,
            },
            "battery": battery,
            "system_uptime": "1 hour",
        }


if __name__ == "__main__":
    unittest.main()
