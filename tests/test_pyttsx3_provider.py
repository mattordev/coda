import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tts.pyttsx3_provider import Pyttsx3Provider, Pyttsx3Session


class Pyttsx3ProviderTests(unittest.TestCase):
    def test_provider_configures_and_reuses_engine(self):
        engine = Mock()
        engine.getProperty.return_value = [
            SimpleNamespace(id="voice-1")
        ]
        engine_factory = Mock(return_value=engine)
        provider = Pyttsx3Provider(
            engine_factory=engine_factory,
            rate=180,
        )

        self.assertTrue(provider.is_available())
        first_session = provider.create_session("first")
        second_session = provider.create_session("second")

        engine_factory.assert_called_once_with()
        engine.setProperty.assert_any_call("rate", 180)
        engine.setProperty.assert_any_call("voice", "voice-1")
        self.assertIs(first_session._engine, engine)
        self.assertIs(second_session._engine, engine)

    def test_provider_reports_initialization_failure(self):
        provider = Pyttsx3Provider(
            engine_factory=Mock(side_effect=RuntimeError("no engine"))
        )

        self.assertFalse(provider.is_available())

    def test_session_completes_using_external_loop(self):
        engine = Mock()
        engine.isBusy.side_effect = [True, False]
        session = Pyttsx3Session("hello", engine)

        played = session.play(threading.Event())

        self.assertTrue(played)
        engine.say.assert_called_once_with("hello")
        engine.startLoop.assert_called_once_with(False)
        self.assertEqual(engine.iterate.call_count, 2)
        engine.endLoop.assert_called_once_with()
        engine.stop.assert_not_called()

    def test_request_cancellation_stops_engine(self):
        engine = Mock()
        cancel_event = threading.Event()
        engine.iterate.side_effect = cancel_event.set
        session = Pyttsx3Session("hello", engine)

        played = session.play(cancel_event)

        self.assertFalse(played)
        engine.stop.assert_called_once_with()
        engine.endLoop.assert_called_once_with()

    def test_session_stop_is_observed_by_playback_loop(self):
        engine = Mock()
        session = Pyttsx3Session("hello", engine)
        engine.iterate.side_effect = session.stop

        played = session.play(threading.Event())

        self.assertFalse(played)
        engine.stop.assert_called_once_with()
        engine.endLoop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
