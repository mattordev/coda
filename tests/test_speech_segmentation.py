import unittest

from tts.segmentation import split_spoken_text


class SpokenTextSegmentationTests(unittest.TestCase):
    def test_empty_text_has_no_segments(self):
        self.assertEqual(split_spoken_text(""), [])
        self.assertEqual(split_spoken_text("   \n"), [])

    def test_single_sentence_remains_one_segment(self):
        self.assertEqual(
            split_spoken_text("Everything is running normally."),
            ["Everything is running normally."],
        )

    def test_splits_complete_sentences_and_keeps_punctuation(self):
        self.assertEqual(
            split_spoken_text(
                "Everything is ready. Would you like me to continue? Yes!"
            ),
            [
                "Everything is ready.",
                "Would you like me to continue?",
                "Yes!",
            ],
        )

    def test_does_not_split_spoken_initialisms(self):
        self.assertEqual(
            split_spoken_text(
                "C. P. U. usage is normal. Everything is ready."
            ),
            [
                "C. P. U. usage is normal.",
                "Everything is ready.",
            ],
        )

    def test_does_not_split_versions_or_decimals(self):
        self.assertEqual(
            split_spoken_text(
                "CODA v1.4.2 is ready. Memory usage is 6.4 gigabytes."
            ),
            [
                "CODA v1.4.2 is ready.",
                "Memory usage is 6.4 gigabytes.",
            ],
        )

    def test_does_not_split_common_abbreviations(self):
        self.assertEqual(
            split_spoken_text("Ask Dr. Smith. Everything is ready."),
            ["Ask Dr. Smith.", "Everything is ready."],
        )

    def test_keeps_closing_quotes_with_the_sentence(self):
        self.assertEqual(
            split_spoken_text(
                'CODA said, "Everything is ready." Shall I continue?'
            ),
            [
                'CODA said, "Everything is ready."',
                "Shall I continue?",
            ],
        )

    def test_conservatively_keeps_acronym_ending_sentence_together(self):
        self.assertEqual(
            split_spoken_text("Use the API. Continue when ready."),
            ["Use the API. Continue when ready."],
        )


if __name__ == "__main__":
    unittest.main()
