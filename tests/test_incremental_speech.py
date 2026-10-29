"""Exercise concurrency at the token/PCM seam without hardware or a model."""
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

from runtime.incremental_speech import IncrementalSpeech
from runtime.messages import SpeechTask, WorkerEventType
from runtime.speech_playback import SpeechPlaybackController
from runtime.speech_worker import SpeechTaskProcessor
from tts.pockettts_provider import PocketTTSProvider
from tests.test_pockettts_provider import FakeProcess


class IncrementalSpeechTests(unittest.TestCase):
    def test_pocket_reuses_one_model_and_process_across_ordered_chunks(self):
        runtime = Mock(sample_rate=24000)
        runtime.stream_pcm.side_effect = lambda text: iter([text.encode()])
        factory = Mock(return_value=runtime)
        provider = PocketTTSProvider(runtime_factory=factory, ffplay_path='ffplay')
        process = FakeProcess()
        with patch('tts.pockettts_provider.Popen', return_value=process) as popen:
            self.assertTrue(provider.create_stream_session(iter(['First.', 'Second.'])).play(Event()))
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(process.stdin.chunks[:2], [b'First.', b'Second.'])
        self.assertEqual([call.args[0] for call in runtime.stream_pcm.call_args_list], ['First.', 'Second.'])

    def test_speech_starts_before_producer_finishes_and_stays_ordered(self):
        cancel = Event()
        started = Event()
        first_spoken = Event()
        spoken = []
        class Provider:
            name = 'synthetic'
            def is_available(self):
                return True
            def create_session(self, text):
                def play(event):
                    spoken.append(text)
                    first_spoken.set()
                    return not event.is_set()
                return Mock(play=play)
        stream = IncrementalSpeech('request', cancel, lambda _: started.set())
        task = SpeechTask('request', '', cancel, stream=stream)
        processor = SpeechTaskProcessor(lambda: [Provider()], SpeechPlaybackController(),
                                        normalise_text=lambda text: text)
        result = []
        thread = Thread(target=lambda: result.append(processor.process(task)))
        stream.feed('First sentence. Next')
        thread.start()
        self.assertTrue(started.wait(1))
        self.assertTrue(first_spoken.wait(1), 'speech must not wait for finish()')
        self.assertFalse(stream.succeeded)
        stream.feed(' sentence.')
        stream.finish()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(spoken, ['First sentence.', 'Next sentence.'])
        self.assertEqual(result[0].event_type, WorkerEventType.COMPLETED)

    def test_cancel_unblocks_full_producer_and_discards_backlog(self):
        cancel = Event()
        stream = IncrementalSpeech('request', cancel, lambda _: None, capacity=1)
        errors = []
        def produce():
            try:
                stream.feed('x' * 4096)
            except InterruptedError:
                errors.append('cancelled')
        thread = Thread(target=produce)
        thread.start()
        # The start callback precedes the bounded put; allow it to fill, then
        # cancellation must release put() even with no speech consumer.
        thread.join(.1)
        self.assertTrue(thread.is_alive())
        self.assertLessEqual(stream._queue.qsize(), 1)
        cancel.set()
        thread.join(1)
        self.assertEqual(errors, ['cancelled'])
        self.assertEqual(list(stream), [])

    def test_abort_discards_queued_text_and_rejects_late_generation(self):
        stream = IncrementalSpeech('request', Event(), lambda _: None)
        stream.feed('Unspoken response.')
        stream.abort()
        self.assertEqual(list(stream), [])
        with self.assertRaises(InterruptedError):
            stream.feed('late')
        stream.finish()
        self.assertFalse(stream.succeeded)

    def test_failure_after_audio_never_replays_on_another_provider(self):
        cancel = Event()
        stream = IncrementalSpeech('request', cancel, lambda _: None)
        stream.feed('First. Second.')
        stream.finish()
        first = Mock(spec=['name', 'is_available', 'create_session'])
        first.is_available.return_value = True
        first.create_session.return_value.play.side_effect = RuntimeError('after audio')
        second = Mock(name='second')
        result = SpeechTaskProcessor(lambda: [first, second], SpeechPlaybackController()).process(
            SpeechTask('request', '', cancel, stream=stream))
        self.assertEqual(result.event_type, WorkerEventType.FAILED)
        second.create_session.assert_not_called()
        self.assertFalse(stream.succeeded)

    def test_empty_stream_and_single_consumer(self):
        start = Mock()
        stream = IncrementalSpeech('request', Event(), start)
        stream.feed('')
        stream.finish()
        self.assertEqual(list(stream), [])
        start.assert_not_called()
        with self.assertRaises(RuntimeError):
            list(stream)


if __name__ == '__main__':
    unittest.main()
