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


class FakeStreamingSpeechSession:
    def __init__(self, chunks, pause_after_chunk=False):
        self.chunks = chunks
        self.pause_after_chunk = pause_after_chunk
        self.played_chunks = []
        self.chunk_played = threading.Event()
        self.release_stream = threading.Event()
        self.stop_called = threading.Event()

    def play(self, cancel_event):
        for chunk in self.chunks:
            if cancel_event.is_set() or self.stop_called.is_set():
                return False

            self.played_chunks.append(chunk)
            self.chunk_played.set()

            if self.pause_after_chunk:
                self.release_stream.wait(timeout=1.0)

        return not cancel_event.is_set() and not self.stop_called.is_set()

    def stop(self):
        self.stop_called.set()
        self.release_stream.set()


class FakeStreamingSpeechProvider:
    name = "future-streaming-provider"

    def __init__(self, chunks, pause_after_chunk=False):
        self.model = object()
        self.chunks = chunks
        self.pause_after_chunk = pause_after_chunk
        self.sessions = []
        self.session_created = threading.Event()

    def is_available(self):
        return True

    def create_session(self, _text):
        session = FakeStreamingSpeechSession(
            self.chunks,
            pause_after_chunk=self.pause_after_chunk,
        )
        self.sessions.append(session)
        self.session_created.set()
        return session


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

    def test_stop_ignores_mismatched_expected_cancel_event(self):
        controller = SpeechPlaybackController()
        session = FakeSpeechSession(block=True)
        provider = FakeSpeechProvider(session)
        active_cancel_event = threading.Event()
        stale_cancel_event = threading.Event()
        stale_cancel_event.set()
        playback_result = []

        worker = threading.Thread(
            target=lambda: playback_result.append(
                controller.play(
                    provider,
                    "newer response",
                    active_cancel_event,
                )
            )
        )
        worker.start()

        try:
            self.assertTrue(session.play_started.wait(timeout=1.0))
            self.assertFalse(
                controller.stop(
                    expected_cancel_event=stale_cancel_event,
                )
            )
            self.assertFalse(active_cancel_event.is_set())
            self.assertFalse(session.stop_called.is_set())
            self.assertTrue(worker.is_alive())
        finally:
            controller.stop(
                expected_cancel_event=active_cancel_event,
            )
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(playback_result, [False])

    def test_stop_interrupts_matching_expected_cancel_event(self):
        controller = SpeechPlaybackController()
        session = FakeSpeechSession(block=True)
        provider = FakeSpeechProvider(session)
        cancel_event = threading.Event()
        playback_result = []

        worker = threading.Thread(
            target=lambda: playback_result.append(
                controller.play(
                    provider,
                    "matching response",
                    cancel_event,
                )
            )
        )
        worker.start()

        self.assertTrue(session.play_started.wait(timeout=1.0))
        self.assertTrue(
            controller.stop(expected_cancel_event=cancel_event)
        )
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertTrue(cancel_event.is_set())
        self.assertTrue(session.stop_called.is_set())
        self.assertEqual(playback_result, [False])

    def test_streaming_provider_uses_existing_session_boundary(self):
        controller = SpeechPlaybackController()
        provider = FakeStreamingSpeechProvider([b"first", b"second"])

        played = controller.play(
            provider,
            "stream this response",
            threading.Event(),
        )

        self.assertTrue(played)
        self.assertEqual(len(provider.sessions), 1)
        self.assertEqual(
            provider.sessions[0].played_chunks,
            [b"first", b"second"],
        )

    def test_streaming_session_can_be_interrupted_between_chunks(self):
        controller = SpeechPlaybackController()
        provider = FakeStreamingSpeechProvider(
            [b"first", b"second"],
            pause_after_chunk=True,
        )
        cancel_event = threading.Event()
        playback_result = []

        worker = threading.Thread(
            target=lambda: playback_result.append(
                controller.play(
                    provider,
                    "interrupt this response",
                    cancel_event,
                )
            )
        )
        worker.start()

        self.assertTrue(provider.session_created.wait(timeout=1.0))
        session = provider.sessions[0]
        self.assertTrue(session.chunk_played.wait(timeout=1.0))
        self.assertTrue(controller.stop())
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(session.played_chunks, [b"first"])
        self.assertEqual(playback_result, [False])
        self.assertTrue(cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
