"""The benchmark reports flow evidence and failed/empty runs honestly."""
from threading import Event
import unittest
from unittest.mock import patch

from benchmarks.incremental_speech import run_trial, SyntheticPCM, SyntheticPlayer
from tts.pockettts_provider import PocketTTSProvider


class IncrementalBenchmarkTests(unittest.TestCase):
    def provider(self):
        return PocketTTSProvider(runtime_factory=lambda *_: SyntheticPCM(), ffplay_path='synthetic')

    def test_full_answer_and_incremental_modes_record_real_stage_order(self):
        audio = Event()
        class ObservedPlayer(SyntheticPlayer):
            def write(self, chunk):
                audio.set()
                return super().write(chunk)
        def generate(callback, cancel):
            callback('First sentence. Next ')
            self.assertTrue(audio.wait(1))
            callback('sentence.')
            return 'First sentence. Next sentence.', None, {}
        with patch('tts.pockettts_provider.Popen', ObservedPlayer):
            row = run_trial('incremental', generate, self.provider())
        self.assertTrue(row['audio_before_generation_complete'])
        self.assertEqual(row['outcome'], 'completed')
        with patch('tts.pockettts_provider.Popen', SyntheticPlayer):
            row = run_trial('full_answer', lambda cb, cancel: ('Short answer.', None, {}), self.provider())
        self.assertFalse(row['audio_before_generation_complete'])
        self.assertLessEqual(row['generation_complete_seconds'], row['first_audio_seconds'])

    def test_empty_and_failed_output_never_claim_audio(self):
        for answer, error, outcome in [('', None, 'empty'), (None, 'failure', 'failed')]:
            row = run_trial('incremental', lambda cb, cancel: (answer, error, {}), self.provider())
            self.assertEqual(row['outcome'], outcome)
            self.assertIsNone(row['first_audio_seconds'])
            self.assertFalse(row['audio_before_generation_complete'])


if __name__ == '__main__':
    unittest.main()
