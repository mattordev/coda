"""Startup readiness includes enabled model preparation, without live models."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event
import unittest
from unittest.mock import patch

import coda_runtime as runtime
from runtime.model_warmup import ModelWarmup


class StartupComplete(Exception):
    """End a fixture before entering the keyboard/microphone loop."""


class RuntimeStartupTests(unittest.TestCase):
    def startup_fixture(self, manager, cancel):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(runtime, 'commands', {}))
        stack.enter_context(patch.object(runtime, 'wakewords', []))
        stack.enter_context(patch.object(runtime, 'manual_assisstant_input', False))
        stack.enter_context(patch.object(runtime, 'shutdown_started', cancel))
        stack.enter_context(patch.object(runtime, '_apply_cli_microphone_flags', return_value=False))
        stack.enter_context(patch.object(runtime, 'load_commands', return_value={}))
        stack.enter_context(patch.object(runtime, 'load_wakewords', return_value=['coda']))
        stack.enter_context(patch.object(runtime.command, 'configure_intent_router'))
        stack.enter_context(patch.object(runtime.command, 'start_tools'))
        stack.enter_context(patch('utils.update_bootstrap.acknowledge_handoff'))
        stack.enter_context(patch.object(runtime.stt_service, 'start'))
        stack.enter_context(patch.object(runtime.model_warmup, 'start', return_value=manager))
        for name in ('start_heartbeat_thread', 'start_execution_thread',
                     'start_event_thread', 'start_speech_thread'):
            stack.enter_context(patch.object(runtime, name))
        stack.enter_context(patch.object(runtime.speech, 'configure_speech_submitter'))
        stack.enter_context(patch.object(runtime.time, 'perf_counter', side_effect=[10, 22.5]))
        printed = stack.enter_context(patch.object(runtime, 'print'))
        voice = stack.enter_context(patch.object(runtime, 'start_voice_thread',
                                                side_effect=StartupComplete))
        return printed, voice

    def test_loaded_message_and_voice_wait_for_all_prewarm_tasks(self):
        entered, release, waiting = Event(), Event(), Event()
        manager = ModelWarmup(sample=lambda: {}, eligible=lambda: True)
        self.addCleanup(manager.close)
        self.addCleanup(release.set)
        def slow(_cancel):
            entered.set()
            release.wait(2)
        manager.start({'llm': lambda _cancel: True, 'tts': slow, 'stt': None})
        self.assertTrue(entered.wait(1))
        printed, voice = self.startup_fixture(manager, Event())
        original_wait = manager.wait
        def wait(name, cancel):
            if name == 'tts':
                waiting.set()
            return original_wait(name, cancel)
        with patch.object(manager, 'wait', side_effect=wait), ThreadPoolExecutor(1) as pool:
            future = pool.submit(runtime._run_runtime, skip_update_check=True)
            try:
                self.assertTrue(waiting.wait(1))
                voice.assert_not_called()
                self.assertFalse(any('C.O.D.A loaded' in call.args[0]
                                     for call in printed.call_args_list))
            finally:
                release.set()
            with self.assertRaises(StartupComplete):
                future.result(timeout=2)
        printed.assert_any_call('C.O.D.A loaded in 12.5 second(s)')
        voice.assert_called_once_with()
        self.assertEqual(manager.snapshot()['tts']['state'], 'ready')

    def test_disabled_or_failed_preparation_does_not_prevent_startup(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                manager = ModelWarmup(sample=lambda: {}, eligible=lambda: True)
                self.addCleanup(manager.close)
                def fail(_cancel):
                    raise RuntimeError('fixture failure')
                manager.start({'llm': fail if failed else None, 'tts': None, 'stt': None})
                printed, voice = self.startup_fixture(manager, Event())
                with self.assertRaises(StartupComplete):
                    runtime._run_runtime(skip_update_check=True)
                voice.assert_called_once_with()
                printed.assert_any_call('C.O.D.A loaded in 12.5 second(s)')

    def test_cancelled_startup_never_announces_loaded_or_starts_voice(self):
        entered, release, cancel = Event(), Event(), Event()
        manager = ModelWarmup(sample=lambda: {}, eligible=lambda: True)
        self.addCleanup(manager.close)
        self.addCleanup(release.set)
        def slow(_cancel):
            entered.set()
            release.wait(2)
        manager.start({'stt': slow})
        self.assertTrue(entered.wait(1))
        printed, voice = self.startup_fixture(manager, cancel)
        cancel.set()
        try:
            self.assertIsNone(runtime._run_runtime(skip_update_check=True))
        finally:
            release.set()
        voice.assert_not_called()
        self.assertFalse(any('C.O.D.A loaded' in call.args[0] for call in printed.call_args_list))
