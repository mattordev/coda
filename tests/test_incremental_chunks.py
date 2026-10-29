"""Token boundaries cannot turn versions, links or code into misleading speech."""
import unittest
from tts.incremental import SpeakableChunks


class IncrementalChunkTests(unittest.TestCase):
    def render(self, text, size=1, **kwargs):
        accumulator = SpeakableChunks(**kwargs)
        chunks = []
        for start in range(0, len(text), size):
            chunks.extend(accumulator.feed(text[start:start+size]))
            self.assertLess(len(accumulator.buffer), accumulator.max_buffer)
        return chunks + accumulator.finish()

    def test_versions_decimals_abbreviations_and_partial_punctuation(self):
        text = 'Dr. Smith uses Python 3.11 and 3.12 at 0.0100. Next sentence! Yes?'
        self.assertEqual(self.render(text), [
            'Dr. Smith uses Python 3.11 and 3.12 at 0.0100.', 'Next sentence!', 'Yes?',
        ])

    def test_urls_links_and_markdown_are_complete_across_all_delta_sizes(self):
        text = '**Read** [the docs](https://example.com/v1.5.2). Then visit https://example.com/a?x=3.11 now.'
        for size in (1, 2, 7, 1000):
            with self.subTest(size=size):
                self.assertEqual(self.render(text, size), [
                    'Read the docs (https://example.com/v1.5.2).',
                    'Then visit https://example.com/a?x=3.11 now.',
                ])

    def test_fenced_code_is_bounded_and_never_spoken(self):
        text = 'First sentence. ```python\n' + 'danger()\n' * 1000 + '``` Final sentence.'
        chunks = self.render(text)
        self.assertNotIn('danger', ' '.join(chunks))
        self.assertIn('Code omitted from speech.', ' '.join(chunks))
        self.assertIn('Final sentence.', chunks)

    def test_timeout_flushes_complete_words_and_holds_trailing_token(self):
        now = [0.0]
        accumulator = SpeakableChunks(clock=lambda: now[0], min_chars=10)
        self.assertEqual(accumulator.feed('This is a useful phrase with version 3.'), [])
        now[0] = 2
        self.assertEqual(accumulator.poll(), ['This is a useful phrase with version'])
        accumulator.feed('11')
        self.assertEqual(accumulator.finish(), ['3.11'])

    def test_long_answers_and_unbroken_tokens_are_bounded(self):
        text = ('A lengthy useful phrase without terminal punctuation ' * 200)
        chunks = self.render(text)
        self.assertEqual(' '.join(' '.join(chunks).split()), ' '.join(text.split()))
        chunks = self.render('x' * 10000 + ' safe text.')
        self.assertNotIn('xxx', ' '.join(chunks))
        self.assertEqual(chunks[-1], 'safe text.')

    def test_empty_short_final_and_finish_are_idempotent(self):
        for text in ('', 'Yes.', 'Python 3.11', '`README.md`'):
            accumulator = SpeakableChunks()
            self.assertEqual(accumulator.feed(text), [])
            self.assertEqual(accumulator.finish(), [text.strip('`')] if text else [])
            self.assertEqual(accumulator.finish(), [])
            with self.assertRaises(ValueError):
                accumulator.feed('late')


if __name__ == '__main__':
    unittest.main()
