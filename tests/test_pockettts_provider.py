import sys
import threading
import types
import unittest
from subprocess import TimeoutExpired
from unittest.mock import Mock, patch

import numpy as np

from tts.pockettts_provider import (
    PocketTTSProvider,
    PocketTTSSession,
    _LoadedPocketTTSRuntime,
    _load_runtime,
)


class FakeStdin:
    def __init__(self, process, fail_writes=False) -> None:
        self._process = process
        self._fail_writes = fail_writes
        self.chunks = []
        self.closed = False

    def write(self, chunk) -> None:
        if self._fail_writes:
            raise BrokenPipeError("ffplay pipe failed")
        if self._process.returncode is not None:
            raise BrokenPipeError("ffplay has stopped")
        self.chunks.append(chunk)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        if self._process.returncode is None:
            self._process.returncode = self._process.exit_code


class FakeProcess:
    def __init__(self, fail_writes=False, exit_code=0) -> None:
        self.returncode = None
        self.exit_code = exit_code
        self.stdin = FakeStdin(self, fail_writes=fail_writes)
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode


class FakeRuntime:
    sample_rate = 24000

    def __init__(
        self,
        chunks=None,
        pause_after_first=False,
        error=None,
    ) -> None:
        self._chunks = chunks or [b"first", b"second"]
        self._pause_after_first = pause_after_first
        self._error = error
        self.first_chunk_consumed = threading.Event()
        self.release_stream = threading.Event()
        self.generated_chunks = []

    def stream_pcm(self, text):
        self.text = text

        for index, chunk in enumerate(self._chunks):
            self.generated_chunks.append(chunk)
            yield chunk

            if index == 0 and self._pause_after_first:
                self.first_chunk_consumed.set()
                self.release_stream.wait(timeout=1.0)

        if self._error is not None:
            raise self._error


class PocketTTSProviderTests(unittest.TestCase):
    def test_runtime_adapter_loads_model_voice_and_float32_pcm(self):
        chunk = Mock()
        chunk.detach.return_value.cpu.return_value.numpy.return_value = np.array(
            [0.25, -0.5],
            dtype=np.float64,
        )
        model = Mock(sample_rate=24000)
        model.generate_audio_stream.return_value = [chunk]
        model_type = Mock()
        model_type.load_model.return_value = model
        pocket_module = types.ModuleType("pocket_tts")
        pocket_module.TTSModel = model_type
        defaults_module = types.ModuleType(
            "pocket_tts.default_parameters"
        )
        default_voice = Mock(return_value="alba")
        defaults_module.__dict__[
            "get_default_voice_for_language"
        ] = default_voice

        with patch.dict(
            sys.modules,
            {
                "pocket_tts": pocket_module,
                "pocket_tts.default_parameters": defaults_module,
            },
        ):
            runtime = _load_runtime("english", None)
            pcm_chunks = list(runtime.stream_pcm("hello"))

        model_type.load_model.assert_called_once_with(
            language="english",
            quantize=False,
        )
        default_voice.assert_called_once_with("english")
        model.get_state_for_audio_prompt.assert_called_once_with("alba")
        self.assertEqual(runtime.sample_rate, 24000)
        self.assertEqual(
            pcm_chunks,
            [np.array([0.25, -0.5], dtype="<f4").tobytes()],
        )

    def test_runtime_serializes_concurrent_generation(self):
        first_started = threading.Event()
        release_first = threading.Event()
        chunk = Mock()
        chunk.detach.return_value.cpu.return_value.numpy.return_value = np.array(
            [0.25],
            dtype=np.float32,
        )
        model = Mock(sample_rate=24000)

        def generate(_state, text):
            if text == "first":
                first_started.set()
                release_first.wait(timeout=1.0)
            yield chunk

        model.generate_audio_stream.side_effect = generate
        runtime = _LoadedPocketTTSRuntime(model, {"voice": "state"})
        workers = [
            threading.Thread(
                target=lambda text=text: list(runtime.stream_pcm(text))
            )
            for text in ("first", "second")
        ]
        workers[0].start()
        self.assertTrue(first_started.wait(timeout=1.0))
        workers[1].start()

        self.assertEqual(model.generate_audio_stream.call_count, 1)
        release_first.set()
        for worker in workers:
            worker.join(timeout=1.0)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(model.generate_audio_stream.call_count, 2)

    def test_provider_retains_configured_runtime(self):
        runtime = FakeRuntime()
        runtime_factory = Mock(return_value=runtime)
        provider = PocketTTSProvider(
            runtime_factory=runtime_factory,
            ffplay_path="ffplay",
            language="english",
            voice="alba",
        )

        self.assertTrue(provider.is_available())
        first_session = provider.create_session("first")
        second_session = provider.create_session("second")

        runtime_factory.assert_called_once_with("english", "alba")
        self.assertIs(first_session._runtime, runtime)
        self.assertIs(second_session._runtime, runtime)

    def test_provider_reads_language_and_voice_from_environment(self):
        runtime_factory = Mock(return_value=FakeRuntime())

        with patch.dict(
            "os.environ",
            {
                "CODA_POCKET_TTS_LANGUAGE": "german_24l",
                "CODA_POCKET_TTS_VOICE": "custom.safetensors",
            },
        ):
            provider = PocketTTSProvider(
                runtime_factory=runtime_factory,
                ffplay_path="ffplay",
            )
            self.assertTrue(provider.is_available())

        runtime_factory.assert_called_once_with(
            "german_24l",
            "custom.safetensors",
        )

    @patch("tts.pockettts_provider.shutil.which", return_value=None)
    def test_provider_is_unavailable_without_ffplay(self, _which):
        runtime_factory = Mock()
        provider = PocketTTSProvider(runtime_factory=runtime_factory)

        self.assertFalse(provider.is_available())
        runtime_factory.assert_not_called()

        with self.assertRaisesRegex(RuntimeError, "ffplay"):
            provider.create_session("hello")

    def test_failed_runtime_is_cached_until_provider_is_replaced(self):
        runtime = FakeRuntime()
        runtime_factory = Mock(
            side_effect=[RuntimeError("missing model"), runtime]
        )
        provider = PocketTTSProvider(
            runtime_factory=runtime_factory,
            ffplay_path="ffplay",
        )

        self.assertFalse(provider.is_available())
        self.assertFalse(provider.is_available())
        self.assertEqual(runtime_factory.call_count, 1)

        provider.close()
        self.assertFalse(provider.is_available())

        replacement = PocketTTSProvider(
            runtime_factory=runtime_factory,
            ffplay_path="ffplay",
        )
        self.assertTrue(replacement.is_available())
        self.assertEqual(runtime_factory.call_count, 2)

    @patch("tts.pockettts_provider.Popen")
    def test_session_streams_float_pcm_to_ffplay(self, popen):
        process = FakeProcess()
        popen.return_value = process
        runtime = FakeRuntime([b"one", b"two"])
        session = PocketTTSSession(
            text="hello",
            runtime=runtime,
            ffplay_path="ffplay",
        )

        played = session.play(threading.Event())

        self.assertTrue(played)
        self.assertEqual(runtime.text, "hello")
        self.assertEqual(process.stdin.chunks[:2], [b"one", b"two"])
        self.assertEqual(len(process.stdin.chunks[2]), 24000 * 4 // 5)
        self.assertEqual(set(process.stdin.chunks[2]), {0})
        self.assertTrue(process.stdin.closed)
        self.assertFalse(session.is_playing())
        command = popen.call_args.args[0]
        self.assertIn("f32le", command)
        self.assertIn("24000", command)
        self.assertIn("-ch_layout", command)
        self.assertIn("mono", command)
        self.assertNotIn("-ac", command)

    @patch("tts.pockettts_provider.Popen")
    def test_stop_ends_playback_and_drains_remaining_audio(self, popen):
        process = FakeProcess()
        popen.return_value = process
        runtime = FakeRuntime(pause_after_first=True)
        session = PocketTTSSession(
            text="cancel me",
            runtime=runtime,
            ffplay_path="ffplay",
        )
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                session.play(threading.Event())
            )
        )
        worker.start()

        self.assertTrue(runtime.first_chunk_consumed.wait(timeout=1.0))
        self.assertTrue(session.is_playing())
        session.stop()
        self.assertFalse(session.is_playing())
        runtime.release_stream.set()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.assertTrue(process.terminated)
        self.assertEqual(process.stdin.chunks, [b"first"])
        self.assertEqual(
            runtime.generated_chunks,
            [b"first", b"second"],
        )

    @patch("tts.pockettts_provider.Popen")
    def test_cancel_event_ends_playback_without_fallback_audio(self, popen):
        process = FakeProcess()
        popen.return_value = process
        runtime = FakeRuntime(pause_after_first=True)
        cancel_event = threading.Event()
        session = PocketTTSSession(
            text="cancel me",
            runtime=runtime,
            ffplay_path="ffplay",
        )
        results = []
        worker = threading.Thread(
            target=lambda: results.append(session.play(cancel_event))
        )
        worker.start()

        self.assertTrue(runtime.first_chunk_consumed.wait(timeout=1.0))
        cancel_event.set()
        runtime.release_stream.set()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.assertTrue(process.terminated)
        self.assertEqual(process.stdin.chunks, [b"first"])

    @patch("tts.pockettts_provider.Popen")
    def test_generation_failure_stops_ffplay_and_propagates(self, popen):
        process = FakeProcess()
        popen.return_value = process
        session = PocketTTSSession(
            text="fail",
            runtime=FakeRuntime(error=RuntimeError("generation failed")),
            ffplay_path="ffplay",
        )

        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            session.play(threading.Event())

        self.assertTrue(process.terminated)
        self.assertFalse(session.is_playing())

    @patch("tts.pockettts_provider.Popen")
    def test_playback_failure_drains_generation_before_propagating(self, popen):
        process = FakeProcess(fail_writes=True)
        popen.return_value = process
        runtime = FakeRuntime()
        session = PocketTTSSession(
            text="drain me",
            runtime=runtime,
            ffplay_path="ffplay",
        )

        with self.assertRaisesRegex(BrokenPipeError, "pipe failed"):
            session.play(threading.Event())

        self.assertEqual(
            runtime.generated_chunks,
            [b"first", b"second"],
        )
        self.assertTrue(process.terminated)

    @patch("tts.pockettts_provider.Popen")
    def test_cancelled_before_play_does_not_start_synthesis(self, popen):
        runtime = Mock(sample_rate=24000)
        session = PocketTTSSession(
            text="do not start",
            runtime=runtime,
            ffplay_path="ffplay",
        )
        cancel_event = threading.Event()
        cancel_event.set()

        self.assertFalse(session.play(cancel_event))
        popen.assert_not_called()
        runtime.stream_pcm.assert_not_called()

    @patch("tts.pockettts_provider.Popen")
    def test_stop_during_process_start_terminates_new_process(self, popen):
        process_starting = threading.Event()
        release_process = threading.Event()
        process = FakeProcess()

        def start_process(*_args, **_kwargs):
            process_starting.set()
            release_process.wait(timeout=1.0)
            return process

        popen.side_effect = start_process
        session = PocketTTSSession(
            text="cancel startup",
            runtime=FakeRuntime(),
            ffplay_path="ffplay",
        )
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                session.play(threading.Event())
            )
        )
        worker.start()

        self.assertTrue(process_starting.wait(timeout=1.0))
        self.assertTrue(session.is_playing())
        session.stop()
        release_process.set()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.assertTrue(process.terminated)

    @patch("tts.pockettts_provider.Popen")
    def test_nonzero_ffplay_exit_reports_failure(self, popen):
        popen.return_value = FakeProcess(exit_code=1)
        session = PocketTTSSession(
            text="bad playback",
            runtime=FakeRuntime(),
            ffplay_path="ffplay",
        )

        self.assertFalse(session.play(threading.Event()))
        self.assertFalse(session.is_playing())

    def test_terminate_timeout_escalates_to_kill(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            TimeoutExpired("ffplay", 1.0),
            0,
        ]

        PocketTTSSession._terminate_process(process)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    @patch("tts.pockettts_provider.Popen")
    def test_process_start_failure_clears_playing_state(self, popen):
        popen.side_effect = RuntimeError("ffplay failed to start")
        session = PocketTTSSession(
            text="fail startup",
            runtime=FakeRuntime(),
            ffplay_path="ffplay",
        )

        with self.assertRaisesRegex(RuntimeError, "failed to start"):
            session.play(threading.Event())

        self.assertFalse(session.is_playing())


if __name__ == "__main__":
    unittest.main()
