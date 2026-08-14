import threading
import unittest
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import Mock, patch

from tts.elevenlabs_provider import (
    ElevenLabsProvider,
    ElevenLabsSession,
)


class ElevenLabsProviderTests(unittest.TestCase):
    @patch(
        "tts.elevenlabs_provider.shutil.which",
        return_value=None,
    )
    def test_provider_is_unavailable_without_ffplay(self, _which):
        provider = ElevenLabsProvider(Mock())

        self.assertFalse(provider.is_available())

        with self.assertRaisesRegex(RuntimeError, "ffplay"):
            provider.create_session("hello")

    @patch("tts.elevenlabs_provider.Popen")
    def test_cancellation_after_generation_skips_playback(self, popen):
        cancel_event = threading.Event()

        def generate_audio(_text):
            cancel_event.set()
            return b"audio"

        session = ElevenLabsSession(
            text="hello",
            generate_audio=generate_audio,
            ffplay_path="ffplay",
        )

        played = session.play(cancel_event)

        self.assertFalse(played)
        popen.assert_not_called()

    @patch("tts.elevenlabs_provider.Popen")
    def test_cancellation_interrupts_wait_for_generation(self, popen):
        generation_started = threading.Event()
        release_generation = threading.Event()
        cancel_event = threading.Event()

        def generate_audio(_text):
            generation_started.set()
            release_generation.wait(timeout=1.0)
            return b"audio"

        session = ElevenLabsSession(
            text="hello",
            generate_audio=generate_audio,
            ffplay_path="ffplay",
        )
        results = []
        worker = threading.Thread(
            target=lambda: results.append(session.play(cancel_event))
        )
        worker.start()

        self.assertTrue(generation_started.wait(timeout=1.0))
        cancel_event.set()
        worker.join(timeout=0.5)
        release_generation.set()

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        popen.assert_not_called()

    @patch.dict("os.environ", {"CODA_ELEVENLABS_TIMEOUT": "0.1"})
    def test_generation_timeout_bounds_provider_wait(self):
        generation_started = threading.Event()
        release_generation = threading.Event()

        def generate_audio(_text):
            generation_started.set()
            release_generation.wait(timeout=1.0)
            return b"audio"

        session = ElevenLabsSession(
            text="hello",
            generate_audio=generate_audio,
            ffplay_path="ffplay",
        )

        with self.assertRaisesRegex(TimeoutError, "generation timed out"):
            session.play(threading.Event())

        self.assertTrue(generation_started.is_set())
        release_generation.set()

    @patch("tts.elevenlabs_provider.Popen")
    def test_completed_ffplay_session_returns_success(self, popen):
        process = Mock()
        process.poll.side_effect = [None, 0]
        process.returncode = 0
        popen.return_value = process
        session = ElevenLabsSession(
            text="hello",
            generate_audio=lambda _text: b"audio",
            ffplay_path="ffplay",
        )

        played = session.play(threading.Event())

        self.assertTrue(played)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "ffplay")
        self.assertIn("-autoexit", command)
        self.assertFalse(Path(command[-1]).exists())
        self.assertFalse(session.is_playing())

    @patch("tts.elevenlabs_provider.Popen")
    def test_session_reports_only_live_ffplay_as_playing(self, popen):
        generation_started = threading.Event()
        release_generation = threading.Event()
        process_started = threading.Event()
        process_finished = threading.Event()

        class ControlledProcess:
            returncode = None

            def poll(self):
                if process_finished.is_set():
                    self.returncode = 0
                    return 0

                return None

        process = ControlledProcess()

        def generate_audio(_text):
            generation_started.set()
            release_generation.wait(timeout=1.0)
            return b"audio"

        def start_process(*_args, **_kwargs):
            process_started.set()
            return process

        popen.side_effect = start_process
        session = ElevenLabsSession(
            text="hello",
            generate_audio=generate_audio,
            ffplay_path="ffplay",
        )
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                session.play(threading.Event())
            )
        )
        worker.start()

        self.assertTrue(generation_started.wait(timeout=1.0))
        self.assertFalse(session.is_playing())
        release_generation.set()
        self.assertTrue(process_started.wait(timeout=1.0))
        self.assertTrue(session.is_playing())
        process_finished.set()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [True])
        self.assertFalse(session.is_playing())

    @patch("tts.elevenlabs_provider.Popen")
    def test_playing_covers_blocked_ffplay_start_and_clears_on_error(
        self,
        popen,
    ):
        process_starting = threading.Event()
        release_process_start = threading.Event()
        errors = []

        def fail_process_start(*_args, **_kwargs):
            process_starting.set()
            release_process_start.wait(timeout=1.0)
            raise RuntimeError("ffplay failed to start")

        popen.side_effect = fail_process_start
        session = ElevenLabsSession(
            text="hello",
            generate_audio=lambda _text: b"audio",
            ffplay_path="ffplay",
        )

        def play():
            try:
                session.play(threading.Event())
            except RuntimeError as error:
                errors.append(error)

        worker = threading.Thread(target=play)
        worker.start()

        self.assertTrue(process_starting.wait(timeout=1.0))
        self.assertTrue(session.is_playing())
        release_process_start.set()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(str(errors[0]), "ffplay failed to start")
        self.assertFalse(session.is_playing())

    @patch("tts.elevenlabs_provider.Popen")
    def test_cancellation_terminates_active_ffplay(self, popen):
        process = Mock()
        process.poll.side_effect = [None, None]
        popen.return_value = process
        cancel_event = Mock()
        cancel_event.is_set.return_value = False
        cancel_event.wait.return_value = True
        session = ElevenLabsSession(
            text="hello",
            generate_audio=lambda _text: b"audio",
            ffplay_path="ffplay",
        )

        played = session.play(cancel_event)

        self.assertFalse(played)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=1.0)
        self.assertFalse(session.is_playing())

    def test_generation_exception_never_reports_playback(self):
        session = ElevenLabsSession(
            text="hello",
            generate_audio=Mock(side_effect=RuntimeError("generation failed")),
            ffplay_path="ffplay",
        )

        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            session.play(threading.Event())

        self.assertFalse(session.is_playing())

    def test_stop_kills_ffplay_when_terminate_times_out(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            TimeoutExpired("ffplay", 1.0),
            0,
        ]
        session = ElevenLabsSession(
            text="hello",
            generate_audio=Mock(),
            ffplay_path="ffplay",
        )
        session._process = process

        session.stop()

        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)


if __name__ == "__main__":
    unittest.main()
