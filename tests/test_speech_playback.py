import threading
import unittest

from runtime.speech_playback import SpeechPlaybackController


class FakeSpeechSession:
    def __init__(self, block=False):
        self.block = block
        self.play_started = threading.Event()
        self.release_playback = threading.Event()
        self.stop_called = threading.Event()

    def play(self, cancel_event):
        self.play_started.set()

        if self.block:
            self.release_playback.wait(timeout=1.0)

        return not self.stop_called.is_set() and not cancel_event.is_set()

    def stop(self):
        self.stop_called.set()
        self.release_playback.set()


class FakeSpeechProvider:
    name = "fake"

    def __init__(self, session):
        self.session = session
        self.created_texts = []

    def is_available(self):
        return True

    def create_session(self, text):
        self.created_texts.append(text)
        return self.session


class SpeechPlaybackControllerTests(unittest.TestCase):
    def test_cancelled_task_does_not_create_session(self):
        controller = SpeechPlaybackController()
        session = FakeSpeechSession()
        provider = FakeSpeechProvider(session)
        cancel_event = threading.Event()
        cancel_event.set()

        played = controller.play(provider, "cancelled", cancel_event)

        self.assertFalse(played)
        self.assertEqual(provider.created_texts, [])

    def test_completed_session_is_cleared(self):
        controller = SpeechPlaybackController()
        session = FakeSpeechSession()
        provider = FakeSpeechProvider(session)

        played = controller.play(
            provider,
            "hello",
            threading.Event(),
        )

        self.assertTrue(played)
        self.assertEqual(provider.created_texts, ["hello"])
        self.assertFalse(controller.stop())

    def test_stop_interrupts_active_session(self):
        controller = SpeechPlaybackController()
        session = FakeSpeechSession(block=True)
        provider = FakeSpeechProvider(session)
        cancel_event = threading.Event()
        playback_result = []

        worker = threading.Thread(
            target=lambda: playback_result.append(
                controller.play(
                    provider,
                    "long response",
                    cancel_event,
                )
            )
        )
        worker.start()

        self.assertTrue(session.play_started.wait(timeout=1.0))
        self.assertTrue(controller.stop())
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertTrue(cancel_event.is_set())
        self.assertTrue(session.stop_called.is_set())
        self.assertEqual(playback_result, [False])
        self.assertFalse(controller.stop())


if __name__ == "__main__":
    unittest.main()
