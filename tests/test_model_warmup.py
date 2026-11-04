"""Warm-up tests use fake models and bounded local HTTP; no downloads or audio."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from threading import Event, Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import Mock, patch

from runtime import model_warmup as warming
from tts.pockettts_provider import PocketTTSProvider
import utils.stt_service as stt
import utils.speak_response as speech
from ai.providers import llamacpp, ollama
from ai.providers.prewarm import warm_local


class ModelWarmupTests(unittest.TestCase):
    def tearDown(self):
        warming.shutdown(0.2)

    def manager(self, **kwargs):
        manager = warming.ModelWarmup(sample=lambda: {'rss_bytes': 100, 'vram_bytes': None},
                                      eligible=lambda: True, **kwargs)
        self.addCleanup(manager.close)
        return manager

    def test_cli_overrides_effective_environment_and_default_is_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            for subsystem in ('llm', 'tts', 'stt'):
                self.assertFalse(warming.enabled(subsystem, []))
                with patch.dict(os.environ, {f'CODA_PREWARM_{subsystem.upper()}': 'true'}):
                    self.assertTrue(warming.enabled(subsystem, []))
                    self.assertFalse(warming.enabled(subsystem, [f'--no-prewarm-{subsystem}']))
                self.assertTrue(warming.enabled(subsystem, [f'--prewarm-{subsystem}']))

    def test_success_failure_skipped_states_and_measurements(self):
        manager = self.manager()
        def fail(_cancel):
            raise RuntimeError('private path and secret must not appear')
        manager.start({'success': lambda _cancel: True, 'failure': fail, 'disabled': None})
        self.assertTrue(manager.wait('success'))
        self.assertTrue(manager.wait('failure'))
        snapshot = manager.snapshot()
        self.assertEqual(snapshot['success']['state'], 'ready')
        self.assertGreaterEqual(snapshot['success']['duration_seconds'], 0)
        self.assertEqual(snapshot['success']['memory_before']['rss_bytes'], 100)
        self.assertEqual(snapshot['failure']['state'], 'failed')
        self.assertNotIn('secret', snapshot['failure']['error'])
        self.assertEqual(snapshot['disabled']['state'], 'skipped')
        snapshot['success']['state'] = 'changed'
        self.assertEqual(manager.snapshot()['success']['state'], 'ready')

    def test_background_start_is_nonblocking_and_wait_is_cancellable(self):
        entered, release, cancel = Event(), Event(), Event()
        manager = self.manager()
        def prepare(_stop):
            entered.set()
            self.assertTrue(release.wait(2))
        manager.start({'llm': prepare})
        self.assertTrue(entered.wait(1))
        self.assertEqual(manager.snapshot()['llm']['state'], 'warming')
        cancel.set()
        self.assertFalse(manager.wait('llm', cancel))
        release.set()
        self.assertTrue(manager.wait('llm'))

    def test_shutdown_is_bounded_and_late_callback_never_publishes_ready(self):
        entered, release = Event(), Event()
        manager = self.manager()
        def prepare(_stop):
            entered.set()
            release.wait(2)
        manager.start({'stt': prepare})
        self.assertTrue(entered.wait(1))
        self.assertFalse(manager.close(0.01))
        release.set()
        self.assertTrue(manager.close(1))
        self.assertEqual(manager.snapshot()['stt']['state'], 'skipped')
        manager.start({'new': lambda _stop: True})
        self.assertNotIn('new', manager.snapshot())

    def test_resource_denial_skips_without_loading(self):
        prepare = Mock()
        manager = warming.ModelWarmup(eligible=lambda: False)
        self.addCleanup(manager.close)
        manager.start({'llm': prepare})
        manager.wait('llm')
        prepare.assert_not_called()
        self.assertEqual(manager.snapshot()['llm']['state'], 'skipped')

    def test_background_loads_are_serial_and_recheck_resource_eligibility(self):
        entered, release = Event(), Event()
        second = Mock()
        def first(_stop):
            entered.set()
            release.wait(2)
        eligible = Mock(side_effect=[True, False])
        manager = warming.ModelWarmup(eligible=eligible)
        self.addCleanup(manager.close)
        manager.start({'first': first, 'second': second})
        self.assertTrue(entered.wait(1))
        second.assert_not_called()
        release.set()
        manager.wait('second')
        self.assertEqual(manager.snapshot()['second']['state'], 'skipped')
        second.assert_not_called()

    def test_reload_retires_before_resetting_models_and_starts_new_generation(self):
        import commands.debug as debug
        events = []
        with patch.object(debug.model_warmup, 'shutdown', side_effect=lambda: events.append('retire')), \
             patch.object(debug.runtime_state, 'reload_dotenv', return_value=(True, 'fixture')), \
             patch.object(debug.command, 'reload_config', side_effect=lambda: events.append('llm')), \
             patch.object(debug.speak_response, 'reload_config', side_effect=lambda **_kwargs: events.append('tts')) as tts, \
             patch.object(debug.stt_service, 'reload_config', side_effect=lambda: events.append('stt')), \
             patch.object(debug.model_warmup, 'start', side_effect=lambda: events.append('start')), \
             patch.object(debug, '_print_status'):
            self.assertTrue(debug._reload_runtime_config())
        self.assertEqual(events, ['retire', 'llm', 'tts', 'stt', 'start'])
        tts.assert_called_once_with(check_availability=False)

    def test_disabled_subsystems_schedule_no_models_or_cloud_providers(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(speech, 'prewarm') as tts, patch.object(stt, 'prewarm') as stt_warm:
            manager = warming.start([])
            self.assertTrue(all(value['state'] == 'skipped' for value in manager.snapshot().values()))
        tts.assert_not_called()
        stt_warm.assert_not_called()

    def test_selection_uses_existing_intent_provider_and_skips_remote_stt(self):
        provider = Mock()
        with patch.dict(os.environ, {'CODA_PREWARM_LLM': 'true', 'CODA_STT_PROVIDER': 'google'}, clear=True), \
             patch('ai.intents.factory._get_local_classifier_provider', return_value=('llamacpp', provider)), \
             patch.object(warming, 'resources_available', return_value=True):
            manager = warming.start([])
            manager.wait('llm')
            self.assertEqual(manager.llm_provider, 'llamacpp')
            self.assertEqual(manager.snapshot()['stt']['state'], 'skipped')
        provider.warmup.assert_called_once()

    def test_first_llm_use_waits_for_shared_warmup_and_cancel_does_not_send_request(self):
        entered, release = Event(), Event()
        manager = self.manager()
        manager.llm_provider = 'llamacpp'
        def prepare(_stop):
            entered.set()
            release.wait(2)
        manager.start({'llm': prepare})
        self.assertTrue(entered.wait(1))
        cancel = Event()
        with patch.object(warming, '_active', manager), patch.object(llamacpp.requests, 'Session') as session:
            cancel.set()
            result = llamacpp.generate_with_metadata([], cancel_event=cancel)
            self.assertIsNotNone(result.error)
            session.assert_not_called()
            self.assertTrue(warming.wait_for_llm('ollama'))
        release.set()


class RetainedModelWarmupTests(unittest.TestCase):
    def setUp(self):
        stt._reset_models(closed=False)
        self.addCleanup(lambda: stt._reset_models(closed=False))

    def test_pocket_warmup_and_first_use_construct_once_and_reuse_same_runtime(self):
        entered, release = Event(), Event()
        runtime = Mock(sample_rate=24000)
        def factory(_language, _voice):
            entered.set()
            self.assertTrue(release.wait(2))
            return runtime
        factory_mock = Mock(side_effect=factory)
        provider = PocketTTSProvider(runtime_factory=factory_mock, ffplay_path='fake-ffplay')
        self.addCleanup(provider.close)
        with ThreadPoolExecutor(2) as pool:
            warm = pool.submit(provider.prewarm, Event())
            self.assertTrue(entered.wait(1))
            first_use = pool.submit(provider._get_runtime)
            release.set()
            self.assertTrue(warm.result(2))
            self.assertIs(first_use.result(2), runtime)
            self.assertIs(provider._get_runtime(), runtime)
        factory_mock.assert_called_once()
        runtime.stream_pcm.assert_not_called()

    def test_pocket_shutdown_during_native_load_discards_late_model(self):
        entered, release = Event(), Event()
        def factory(_language, _voice):
            entered.set()
            release.wait(2)
            return Mock()
        provider = PocketTTSProvider(runtime_factory=factory, ffplay_path='fake')
        with ThreadPoolExecutor(1) as pool:
            loading = pool.submit(provider._get_runtime)
            self.assertTrue(entered.wait(1))
            provider.close()
            release.set()
            with self.assertRaises(RuntimeError):
                loading.result(2)
        self.assertIsNone(provider._runtime)
        self.assertIsNone(provider._load_error)

    def test_whisper_warmup_and_first_use_share_one_model(self):
        entered, release = Event(), Event()
        model = Mock()
        def load(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            return model
        attempt = ('small', 'cpu', 'int8')
        with patch.object(stt, 'WhisperModel', side_effect=load) as constructor, \
             patch.object(stt, '_get_stt_provider', return_value='faster-whisper'), \
             patch.object(stt, '_get_available_whisper_attempts', return_value=[attempt]), ThreadPoolExecutor(2) as pool:
            warm = pool.submit(stt.prewarm, Event())
            self.assertTrue(entered.wait(1))
            first_use = pool.submit(stt._load_whisper_model, *attempt)
            release.set()
            self.assertTrue(warm.result(2))
            self.assertIs(first_use.result(2), model)
            constructor.assert_called_once()

    def test_whisper_reload_and_shutdown_invalidate_inflight_loads(self):
        for closed in (False, True):
            stt._reset_models(closed=False)
            entered, release = Event(), Event()
            def load(*_args, **_kwargs):
                entered.set()
                release.wait(2)
                return Mock()
            with self.subTest(closed=closed), patch.object(stt, 'WhisperModel', side_effect=load), ThreadPoolExecutor(1) as pool:
                pending = pool.submit(stt._load_whisper_model, 'small', 'cpu', 'int8')
                self.assertTrue(entered.wait(1))
                stt._reset_models(closed=closed)
                release.set()
                with self.assertRaises(stt.SpeechToTextError):
                    pending.result(2)
                self.assertEqual(stt.whisper_model_cache, {})

    def test_tts_warming_never_resolves_cloud_or_generates_audio(self):
        provider = Mock()
        with patch.dict(os.environ, {'TTS_PROVIDER_ORDER': 'elevenlabs,pockettts,pyttsx3'}, clear=True), \
             patch.object(speech, '_resolve_pockettts_provider', return_value=provider), \
             patch.object(speech, '_resolve_elevenlabs_provider') as cloud:
            speech.prewarm(Event())
        cloud.assert_not_called()
        provider.prewarm.assert_called_once()
        provider.create_session.assert_not_called()

    def test_local_llm_warming_uses_one_token_and_configured_model(self):
        for module, protocol in ((llamacpp, 'llamacpp'), (ollama, 'ollama')):
            with self.subTest(provider=protocol), patch.object(module, 'get_model', return_value=('configured-model', None)), \
                 patch('ai.providers.prewarm.requests.Session') as constructor:
                session = constructor.return_value
                response = session.post.return_value.__enter__.return_value
                response.iter_content.return_value = [b'{"done":true,"choices":[{"finish_reason":"length"}]}']
                warm_local(module, protocol, Event())
                payload = session.post.call_args.kwargs['json']
                self.assertEqual(payload['model'], 'configured-model')
                self.assertFalse(payload['stream'])
                self.assertEqual(payload.get('max_tokens', payload.get('options', {}).get('num_predict')), 1)
                session.close.assert_called_once()
                response.close.assert_called_once()

    def test_real_local_http_warmup_is_shared_with_first_inference(self):
        entered, release = Event(), Event()
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(payload)
                if not payload['stream']:
                    entered.set()
                    release.wait(3)
                    data = b'{"choices":[{"message":{"content":"Hi"}}]}'
                else:
                    data = b'data: {"choices":[{"delta":{"content":"Ready"}}]}\n\ndata: [DONE]\n\n'
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        manager = warming.ModelWarmup(eligible=lambda: True)
        manager.llm_provider = 'llamacpp'
        try:
            with patch.dict(os.environ, {'CODA_LLAMACPP_MODEL': 'fixture',
                                        'CODA_LLAMACPP_BASE_URL': f'http://127.0.0.1:{server.server_port}'}), \
                 patch.object(warming, '_active', manager), ThreadPoolExecutor(1) as pool:
                manager.start({'llm': llamacpp.warmup})
                self.assertTrue(entered.wait(2))
                inference = pool.submit(llamacpp.generate, [{'role': 'user', 'content': 'test'}])
                self.assertEqual(len(calls), 1)
                release.set()
                self.assertEqual(inference.result(3), ('Ready', None))
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0]['max_tokens'], 1)
                self.assertEqual(calls[0]['model'], calls[1]['model'])
        finally:
            release.set()
            manager.close(1)
            server.shutdown()
            server.server_close()
            thread.join(1)
