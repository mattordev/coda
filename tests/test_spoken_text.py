import unittest

from tts.spoken_text import normalise_spoken_text


class SpokenTextNormalisationTests(unittest.TestCase):
    def test_normalises_common_system_metrics(self):
        cases = (
            (
                "CPU usage is 37 percent.",
                "See pee you usage is thirty-seven percent.",
            ),
            (
                "GPU usage is 12.5 percent.",
                "Gee pee you usage is twelve point five percent.",
            ),
            (
                "Memory usage is 6.4 gigabytes.",
                "Memory usage is six point four gigabytes.",
            ),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(
                    normalise_spoken_text(original),
                    expected,
                )

    def test_leaves_natural_language_unchanged(self):
        text = "Everything is running normally."

        self.assertEqual(normalise_spoken_text(text), text)

    def test_normalises_standalone_numbers(self):
        cases = (
            ("There are 37 tasks.", "There are thirty-seven tasks."),
            ("The result is 12.5.", "The result is twelve point five."),
            (
                "There are 4,568 tasks.",
                "There are four thousand, five hundred and sixty-eight tasks.",
            ),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(
                    normalise_spoken_text(original),
                    expected,
                )

    def test_normalises_system_initialisms(self):
        cases = (
            ("The API is ready.", "The A. P. I. is ready."),
            ("cpu usage is normal.", "See pee you usage is normal."),
            ("The gpu is ready.", "The gee pee you is ready."),
            ("Check the CPU.", "Check the see pee you."),
            (
                "CPU, GPU usage is normal.",
                "See pee you, gee pee you usage is normal.",
            ),
            ("RAM is available.", "ram is available."),
            ("Open the URL now.", "Open the U. R. L. now."),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(
                    normalise_spoken_text(original),
                    expected,
                )

    def test_normalises_contextual_units(self):
        cases = (
            ("Memory usage is 1 GB.", "Memory usage is one gigabyte."),
            ("Memory usage is 6.4 GB.", "Memory usage is six point four gigabytes."),
            ("The model uses 512 MB.", "The model uses five hundred and twelve megabytes."),
            ("Latency is 1 ms.", "Latency is one millisecond."),
            ("Latency is 218 ms.", "Latency is two hundred and eighteen milliseconds."),
            (
                "The CPU runs at 3.6 GHz.",
                "The see pee you runs at three point six gigahertz.",
            ),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(
                    normalise_spoken_text(original),
                    expected,
                )

    def test_normalises_currency(self):
        cases = (
            (
                "The charge is \N{POUND SIGN}1.",
                "The charge is one pound.",
            ),
            (
                "The charge is \N{POUND SIGN}12.50.",
                "The charge is twelve pounds and fifty pence.",
            ),
            ("The charge is $1.", "The charge is one dollar."),
            (
                "The charge is $12.50.",
                "The charge is twelve dollars and fifty cents.",
            ),
            (
                "The charge is \N{POUND SIGN}1,250.50.",
                "The charge is one thousand, two hundred and fifty pounds "
                "and fifty pence.",
            ),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(
                    normalise_spoken_text(original),
                    expected,
                )

    def test_normalises_percentage_symbols(self):
        cases = (
            (
                "CPU usage is 37%.",
                "See pee you usage is thirty-seven percent.",
            ),
            (
                "GPU usage is 12.5 %.",
                "Gee pee you usage is twelve point five percent.",
            ),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(
                    normalise_spoken_text(original),
                    expected,
                )

    def test_normalises_twenty_four_hour_times(self):
        cases = (
            (
                "The check runs at 09:05 tomorrow.",
                "The check runs at nine oh five A. M. tomorrow.",
            ),
            (
                "The check runs at 18:30 tomorrow.",
                "The check runs at six thirty P. M. tomorrow.",
            ),
            (
                "The day starts at 00:00 exactly.",
                "The day starts at midnight exactly.",
            ),
            (
                "The check runs at 12:00 exactly.",
                "The check runs at noon exactly.",
            ),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(
                    normalise_spoken_text(original),
                    expected,
                )

    def test_normalises_unambiguous_english_dates(self):
        cases = (
            (
                "The release is on 22 August 2026.",
                "The release is on the twenty-second of August, "
                "twenty twenty-six.",
            ),
            (
                "The release is on 1 January 2000.",
                "The release is on the first of January, two thousand.",
            ),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(
                    normalise_spoken_text(original),
                    expected,
                )

    def test_preserves_ambiguous_dotted_and_path_tokens(self):
        unchanged_text = (
            "CODA version v1.4.2 is ready.",
            "Service 192.168.1.42 is available.",
            "Visit https://openrouter.ai.",
            "Visit https://example.com/API/v1/report?id=37.",
            "Read README.md.",
            r"Open C:\Models\voice1.wav.",
            "The configured date is 22/08/2026.",
            "The configured date is 2026-08-22.",
            "The value 18:99 is not a valid time.",
        )

        for text in unchanged_text:
            with self.subTest(text=text):
                self.assertEqual(normalise_spoken_text(text), text)

    def test_normalisation_is_idempotent(self):
        text = (
            "CPU usage is 37%, memory usage is 6.4 GB, the charge is "
            "\N{POUND SIGN}12.50, and the check is at 18:30 on "
            "22 August 2026."
        )

        normalised = normalise_spoken_text(text)

        self.assertEqual(normalise_spoken_text(normalised), normalised)


if __name__ == "__main__":
    unittest.main()
