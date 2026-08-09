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
