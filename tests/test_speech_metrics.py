"""Timings start at acceptance; missing audio stays unknown and logs contain no text."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from runtime import speech_metrics
from runtime.speech_metrics import SpeechMetrics
from tts.pockettts_provider import PocketTTSSession
from tests.test_pockettts_provider import FakeProcess


class SpeechMetricTests(unittest.TestCase):
    def test_first_stage_is_retained_and_unknown_fields_are_not_zero(self):
        now = [12.0]
        records = []
        metrics = SpeechMetrics('request', 10.0, clock=lambda: now[0], record=records.append)
        metrics.mark('first_visible')
        now[0] = 15
        metrics.mark('first_visible')
        metrics.mark('generation_complete')
        metrics.finish('completed')
        metrics.finish('failed')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['first_visible_seconds'], 2.0)
        self.assertEqual(records[0]['generation_complete_seconds'], 5.0)
        self.assertEqual(records[0]['completion_seconds'], 5.0)
        self.assertIsNone(records[0]['first_pcm_seconds'])
        self.assertIsNone(records[0]['first_audio_seconds'])
        self.assertEqual(records[0]['outcome'], 'completed')

    def test_pcm_is_observed_before_successful_pipe_write(self):
        stages = []
        metrics = Mock(mark=stages.append)
        process = FakeProcess()
        runtime = Mock(sample_rate=24000, stream_pcm=lambda text: iter([b'PCM']))
        session = PocketTTSSession('secret answer', runtime, 'ffplay', metrics=metrics)
        with patch('tts.pockettts_provider.Popen', return_value=process):
            self.assertTrue(session.play(Event()))
        self.assertEqual(stages, ['first_pcm', 'playback_start'])
        stages.clear()
        process = FakeProcess(fail_writes=True)
        with patch('tts.pockettts_provider.Popen', return_value=process), self.assertRaises(BrokenPipeError):
            PocketTTSSession('text', runtime, 'ffplay', metrics=metrics).play(Event())
        self.assertEqual(stages, ['first_pcm'])

    def test_logs_are_bounded_content_free_and_io_failure_is_nonfatal(self):
        from collections import deque
        with TemporaryDirectory() as directory, \
             patch.object(speech_metrics, '_PATH', Path(directory) / 'active.jsonl'), \
             patch.object(speech_metrics, '_RECORDS', deque(maxlen=2)):
            for number in range(3):
                SpeechMetrics(str(number), 0).finish('completed')
            records = [json.loads(line) for line in speech_metrics._PATH.read_text().splitlines()]
            self.assertEqual([record['request_id'] for record in records], ['1', '2'])
            self.assertNotIn('text', records[0])
            with patch.object(Path, 'mkdir', side_effect=OSError('read only')):
                SpeechMetrics('safe', 0).finish('completed')


if __name__ == '__main__':
    unittest.main()
