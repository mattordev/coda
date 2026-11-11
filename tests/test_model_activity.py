"""Model status stays truthful, debug gated, and independent of real models."""
from io import StringIO
import os
from threading import Event
import unittest
from unittest.mock import patch

from utils.model_activity import ModelActivity
from tts.pockettts_provider import PocketTTSProvider
import utils.stt_service as stt


class ModelActivityTests(unittest.TestCase):
    def test_disabled_debug_starts_no_worker_and_prints_nothing(self):
        with patch.dict(os.environ, {'CODA_DEBUG': 'false'}), \
             patch('utils.model_activity.Thread') as thread, patch('builtins.print') as printed:
            with ModelActivity('STT', 'loading') as activity:
                activity.stage('preparing')
            thread.assert_not_called()
            printed.assert_not_called()

    def test_stages_elapsed_and_periodic_progress_stop_after_finish(self):
        output, reported = StringIO(), Event()
        with patch.dict(os.environ, {'CODA_DEBUG': 'true'}), patch('sys.stdout', output):
            with ModelActivity('STT', 'loading', interval=0.01) as activity:
                original = activity._report
                def report(stage):
                    original(stage)
                    reported.set()
                activity._report = report
                self.assertTrue(reported.wait(1))
                activity.stage('preparing')
                activity.finish()
                length = len(output.getvalue())
                activity.stage('late')
                activity.finish()
                self.assertEqual(len(output.getvalue()), length)
            self.assertFalse(activity._worker.is_alive())
        text = output.getvalue()
        self.assertGreaterEqual(text.count('loading'), 2)
        self.assertIn('preparing', text)
        self.assertIn('ready', text)
        self.assertIn('elapsed', text)
        self.assertNotIn('%', text)

    def test_failure_and_cancellation_never_claim_ready_or_print_exception(self):
        for cancelled in (False, True):
            output, cancel = StringIO(), Event()
            with patch.dict(os.environ, {'CODA_DEBUG': 'true'}), patch('sys.stdout', output):
                with self.assertRaises(RuntimeError):
                    with ModelActivity('TTS', 'loading', cancel_event=cancel):
                        if cancelled:
                            cancel.set()
                        raise RuntimeError('secret path and credentials')
            self.assertNotIn('ready', output.getvalue())
            self.assertNotIn('secret', output.getvalue())
            self.assertIn('cancelled' if cancelled else 'failed', output.getvalue())

    def test_cached_pocket_and_whisper_do_not_report_loading_again(self):
        output, loaded = StringIO(), object()
        provider = PocketTTSProvider(runtime_factory=lambda *_args: loaded, ffplay_path='fixture')
        self.addCleanup(provider.close)
        # Isolate the real cache/lifecycle code without resetting user configuration.
        with patch.dict(os.environ, {'CODA_DEBUG': 'true'}), patch('sys.stdout', output), \
             patch.object(stt, 'WhisperModel', return_value=loaded) as factory, \
             patch.object(stt, 'whisper_model_cache', {}), patch.object(stt, '_whisper_closed', False):
            self.assertIs(provider._get_runtime(), loaded)
            self.assertIs(stt._load_whisper_model('fixture', 'cpu', 'int8'), loaded)
            first_output = output.getvalue()
            self.assertIn('TTS Pocket', first_output)
            self.assertIn('STT faster-whisper', first_output)
            self.assertIs(provider._get_runtime(), loaded)
            self.assertIs(stt._load_whisper_model('fixture', 'cpu', 'int8'), loaded)
            self.assertEqual(output.getvalue(), first_output)
            factory.assert_called_once()
