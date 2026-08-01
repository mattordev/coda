import datetime
import unittest
from unittest.mock import patch

from ai.intents import (
    ExactMatchStrategy,
    ExampleMatchStrategy,
    IntentRegistry,
    IntentRequest,
    IntentRouter,
)
from commands import time as time_command


class TimeCommandTests(unittest.TestCase):
    def test_formats_ordinal_suffixes(self):
        cases = (
            (1, "1st"),
            (2, "2nd"),
            (3, "3rd"),
            (4, "4th"),
            (11, "11th"),
            (12, "12th"),
            (13, "13th"),
            (21, "21st"),
            (22, "22nd"),
            (23, "23rd"),
            (31, "31st"),
        )

        for number, expected in cases:
            with self.subTest(number=number):
                result = time_command.format_ordinal(number)

                self.assertEqual(result, expected)

    def test_formats_spoken_date(self):
        # Arrange
        fixed_date = datetime.datetime(2026, 1, 1)

        # Act
        result = time_command.format_spoken_date(fixed_date)

        # Assert
        self.assertEqual(result, "the 1st of January 2026")

    def test_collects_consistent_date_and_time_formats(self):
        fixed_date_time = datetime.datetime(2026, 7, 23, 8, 5)

        with patch("commands.time.datetime.datetime") as datetime_type:
            datetime_type.now.return_value = fixed_date_time

            result = time_command.get_date_time()

        datetime_type.now.assert_called_once_with()
        self.assertEqual(
            result,
            {
                "time": "08:05 AM",
                "spoken_time": "8:05 AM",
                "date": "23/07/2026",
                "spoken_date": "the 23rd of July 2026",
            },
        )

    def test_formats_midnight_midday_and_evening(self):
        cases = (
            (0, "12:05 AM", "12:05 AM"),
            (12, "12:05 PM", "12:05 PM"),
            (20, "08:05 PM", "8:05 PM"),
        )

        for hour, expected_time, expected_spoken_time in cases:
            with self.subTest(hour=hour):
                fixed_date_time = datetime.datetime(2026, 1, 1, hour, 5)

                with patch("commands.time.datetime.datetime") as datetime_type:
                    datetime_type.now.return_value = fixed_date_time

                    result = time_command.get_date_time()

                self.assertEqual(result["time"], expected_time)
                self.assertEqual(
                    result["spoken_time"],
                    expected_spoken_time,
                )

    def test_reports_current_time(self):
        request = self._request("TIME")

        with (
            patch.object(
                time_command,
                "get_date_time",
                return_value=self._date_time_data(),
            ),
            patch("builtins.print") as print_output,
            patch.object(time_command.speak, "speak_response") as speak_response,
        ):
            result = time_command.run(request)

        self.assertTrue(result)
        print_output.assert_called_once_with("The current time is 08:05 AM.")
        speak_response.assert_called_once_with(
            "The time is currently 8:05 AM."
        )

    def test_reports_current_date(self):
        request = self._request("DATE")

        with (
            patch.object(
                time_command,
                "get_date_time",
                return_value=self._date_time_data(),
            ),
            patch("builtins.print") as print_output,
            patch.object(time_command.speak, "speak_response") as speak_response,
        ):
            result = time_command.run(request)

        self.assertTrue(result)
        print_output.assert_called_once_with("The current date is 01/01/2026.")
        speak_response.assert_called_once_with(
            "The current date is the 1st of January 2026."
        )

    def test_routes_aliases_and_examples(self):
        registry = IntentRegistry()
        registry.register(time_command.INTENT)
        router = IntentRouter(
            registry,
            (ExactMatchStrategy(), ExampleMatchStrategy()),
        )
        cases = (
            ("current time", "exact_match"),
            ("date", "exact_match"),
            ("what time is it", "example_match"),
            ("what date is it", "example_match"),
        )

        for message, expected_strategy in cases:
            with self.subTest(message=message):
                result = router.route(message)

                self.assertTrue(result.accepted)
                self.assertIs(result.intent, time_command.INTENT)
                self.assertEqual(result.strategy, expected_strategy)

    @staticmethod
    def _request(message):
        return IntentRequest(
            intent=time_command.INTENT,
            message=message,
            confidence=1.0,
            strategy="test",
        )

    @staticmethod
    def _date_time_data():
        return {
            "time": "08:05 AM",
            "spoken_time": "8:05 AM",
            "date": "01/01/2026",
            "spoken_date": "the 1st of January 2026",
        }


if __name__ == "__main__":
    unittest.main()
