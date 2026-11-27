from datetime import UTC, datetime
import unittest

from runtime.schedule_time import schedule_timestamp


class ScheduleTimeTests(unittest.TestCase):
    def test_converts_a_normal_local_time(self):
        timestamp = schedule_timestamp("2026-07-15T09:00", "Europe/London")
        self.assertEqual(datetime.fromtimestamp(timestamp, UTC), datetime(2026, 7, 15, 8, tzinfo=UTC))

    def test_rejects_the_spring_gap_even_with_an_offset(self):
        for suffix in ("", "+00:00", "+01:00"):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "does not occur"):
                schedule_timestamp("2026-03-29T01:30" + suffix, "Europe/London")

    def test_autumn_repeat_requires_an_offset_that_matches_the_zone(self):
        with self.assertRaisesRegex(ValueError, "occurs twice"):
            schedule_timestamp("2026-10-25T01:30", "Europe/London")
        early = schedule_timestamp("2026-10-25T01:30+01:00", "Europe/London")
        late = schedule_timestamp("2026-10-25T01:30+00:00", "Europe/London")
        self.assertEqual(late - early, 3600)
        with self.assertRaisesRegex(ValueError, "does not occur"):
            schedule_timestamp("2026-10-25T01:30+02:00", "Europe/London")
