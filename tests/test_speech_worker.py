import threading
import unittest
from unittest.mock import Mock, patch

from runtime.messages import (
    SpeechTask,
    WorkerEvent,
    WorkerEventType,
    WorkerName,
)
from runtime.runtime_queue import RuntimeQueues
from runtime.speech_playback import SpeechPlaybackController
from runtime.speech_worker import SpeechTaskProcessor, SpeechWorker


class FakeProvider:
    def __init__(self, name, available=True):
        self.name = name
        self._available = available

    def is_available(self):
        return self._available


class RecordingStreamingSession:
    def __init__(self, text, sink, pause_after_first=False):
        self._text = text
        self._sink = sink
        self._pause_after_first = pause_after_first
        self._stop_event = threading.Event()
        self._release_stream = threading.Event()
        self.first_frame_played = threading.Event()
        self._playing = False

    def play(self, cancel_event):
        self._playing = True

        try:
            for index, frame in enumerate((b"first", b"second")):
                if cancel_event.is_set() or self._stop_event.is_set():
                    return False

                self._sink.append((self._text, frame))

                if index == 0:
                    self.first_frame_played.set()

                    if self._pause_after_first:
                        self._release_stream.wait(timeout=1.0)

            return not cancel_event.is_set() and not self._stop_event.is_set()
        finally:
            self._playing = False

    def stop(self):
        self._stop_event.set()
        self._release_stream.set()

    def is_playing(self):
        return self._playing


class RecordingStreamingProvider:
    name = "streaming"

    def __init__(self):
        self.played_frames = []
        self.sessions = []
        self.session_created = threading.Event()

    def is_available(self):
        return True

    def create_session(self, text):
        session = RecordingStreamingSession(
            text,
            self.played_frames,
            pause_after_first=(text == "cancel this response"),
        )
        self.sessions.append(session)
        self.session_created.set()
        return session


class SpeechTaskProcessorTests(unittest.TestCase):
    def _task(self):
        return SpeechTask(
            request_id="request-1",
            text="hello",
            cancel_event=threading.Event(),
        )

    def test_cancelled_task_skips_provider_resolution(self):
        task = self._task()
        task.cancel_event.set()
        resolve_providers = Mock()
        controller = Mock(spec=SpeechPlaybackController)
        normaliser = Mock()
        processor = SpeechTaskProcessor(
            resolve_providers,
            controller,
            normalise_text=normaliser,
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.CANCELLED)
        self.assertEqual(event.worker, WorkerName.SPEECH)
        self.assertEqual(event.request_id, task.request_id)
        resolve_providers.assert_not_called()
        controller.play.assert_not_called()
        normaliser.assert_not_called()

    def test_uses_first_available_successful_provider(self):
        unavailable = FakeProvider("unavailable", available=False)
        available = FakeProvider("available")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.return_value = True
        task = self._task()
        processor = SpeechTaskProcessor(
            lambda: [unavailable, available],
            controller,
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        controller.play.assert_called_once_with(
            available,
            task.text,
            task.cancel_event,
        )

    @patch("runtime.speech_worker.runtime_state.debug_print")
    def test_provider_error_falls_back_to_next_provider(self, debug_print):
        first = FakeProvider("first")
        second = FakeProvider("second")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.side_effect = [
            RuntimeError("playback failed"),
            True,
        ]
        processor = SpeechTaskProcessor(
            lambda: [first, second],
            controller,
        )

        event = processor.process(self._task())

        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        self.assertEqual(controller.play.call_count, 2)
        debug_print.assert_any_call(
            "[TTS] first: playback failed. Trying next provider."
        )
        debug_print.assert_any_call(
            "[TTS] Fallback provider second succeeded."
        )

    def test_incomplete_playback_without_cancellation_falls_back(self):
        first = FakeProvider("first")
        second = FakeProvider("second")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.side_effect = [False, True]
        task = self._task()
        processor = SpeechTaskProcessor(
            lambda: [first, second],
            controller,
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        self.assertFalse(task.cancel_event.is_set())
        self.assertEqual(
            [call.args[0] for call in controller.play.call_args_list],
            [first, second],
        )

    def test_availability_error_falls_back_to_next_provider(self):
        first = Mock(name="first_provider")
        first.name = "first"
        first.is_available.side_effect = RuntimeError("check failed")
        second = FakeProvider("second")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.return_value = True
        processor = SpeechTaskProcessor(
            lambda: [first, second],
            controller,
        )

        event = processor.process(self._task())

        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        controller.play.assert_called_once()
        self.assertIs(controller.play.call_args.args[0], second)

    def test_all_provider_failures_report_ordered_errors(self):
        first = FakeProvider("first")
        second = FakeProvider("second")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.side_effect = [
            RuntimeError("generation failed"),
            False,
        ]
        processor = SpeechTaskProcessor(
            lambda: [first, second],
            controller,
        )

        event = processor.process(self._task())

        self.assertEqual(event.event_type, WorkerEventType.FAILED)
        self.assertEqual(
            event.error,
            "first: generation failed; "
            "second: playback did not complete.",
        )

    def test_normalises_text_once_and_reuses_it_for_fallback(self):
        first = FakeProvider("first")
        second = FakeProvider("second")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.side_effect = [RuntimeError("failed"), True]
        spoken_text = "See pee you usage is thirty-seven percent."
        normaliser = Mock(return_value=spoken_text)
        task = SpeechTask(
            request_id="request-1",
            text="CPU usage is 37 percent.",
            cancel_event=threading.Event(),
        )
        processor = SpeechTaskProcessor(
            lambda: [first, second],
            controller,
            normalise_text=normaliser,
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        normaliser.assert_called_once_with("CPU usage is 37 percent.")
        self.assertEqual(
            [call.args[1] for call in controller.play.call_args_list],
            [spoken_text, spoken_text],
        )
        self.assertEqual(task.text, "CPU usage is 37 percent.")

    def test_processes_spoken_segments_in_order(self):
        provider = FakeProvider("primary")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.return_value = True
        segmenter = Mock(return_value=["First sentence.", "Second sentence."])
        task = self._task()
        processor = SpeechTaskProcessor(
            lambda: [provider],
            controller,
            segment_text=segmenter,
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        segmenter.assert_called_once_with(task.text)
        self.assertEqual(
            [call.args[1] for call in controller.play.call_args_list],
            ["First sentence.", "Second sentence."],
        )

    def test_phonetic_initialism_preserves_sentence_boundary(self):
        provider = FakeProvider("primary")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.return_value = True
        task = SpeechTask(
            request_id="request-1",
            text=(
                "CODA is running normally. CPU usage is 37 percent, "
                "GPU usage is 12.5 percent."
            ),
            cancel_event=threading.Event(),
        )
        processor = SpeechTaskProcessor(
            lambda: [provider],
            controller,
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        self.assertEqual(
            [call.args[1] for call in controller.play.call_args_list],
            [
                "CODA is running normally.",
                (
                    "See pee you usage is thirty-seven percent, "
                    "gee pee you usage is twelve point five percent."
                ),
            ],
        )

    def test_failure_falls_back_only_for_incomplete_segment(self):
        primary = FakeProvider("primary")
        fallback = FakeProvider("fallback")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.side_effect = [
            True,
            RuntimeError("generation failed"),
            True,
            True,
        ]
        processor = SpeechTaskProcessor(
            lambda: [primary, fallback],
            controller,
            segment_text=lambda _text: ["First.", "Second.", "Third."],
        )

        event = processor.process(self._task())

        self.assertEqual(event.event_type, WorkerEventType.COMPLETED)
        self.assertEqual(
            [
                (call.args[0], call.args[1])
                for call in controller.play.call_args_list
            ],
            [
                (primary, "First."),
                (primary, "Second."),
                (fallback, "Second."),
                (fallback, "Third."),
            ],
        )

    def test_cancellation_on_later_segment_does_not_fall_back(self):
        primary = FakeProvider("primary")
        fallback = FakeProvider("fallback")
        controller = Mock(spec=SpeechPlaybackController)
        task = self._task()

        def play(_provider, segment, _cancel_event):
            if segment == "Second.":
                task.cancel_event.set()
                return False
            return True

        controller.play.side_effect = play
        processor = SpeechTaskProcessor(
            lambda: [primary, fallback],
            controller,
            segment_text=lambda _text: ["First.", "Second.", "Third."],
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.CANCELLED)
        self.assertEqual(
            [
                (call.args[0], call.args[1])
                for call in controller.play.call_args_list
            ],
            [(primary, "First."), (primary, "Second.")],
        )

    def test_cancellation_during_playback_prevents_fallback(self):
        first = FakeProvider("first")
        second = FakeProvider("second")
        controller = Mock(spec=SpeechPlaybackController)
        task = self._task()

        def cancel_during_playback(*_args):
            task.cancel_event.set()
            return False

        controller.play.side_effect = cancel_during_playback
        processor = SpeechTaskProcessor(
            lambda: [first, second],
            controller,
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.CANCELLED)
        controller.play.assert_called_once()

    def test_no_available_provider_returns_failure(self):
        processor = SpeechTaskProcessor(
            lambda: [FakeProvider("offline", available=False)],
            Mock(spec=SpeechPlaybackController),
        )

        event = processor.process(self._task())

        self.assertEqual(event.event_type, WorkerEventType.FAILED)
        self.assertEqual(event.error, "No TTS provider is available.")


class SpeechWorkerTests(unittest.TestCase):
    @staticmethod
    def _task(request_id):
        return SpeechTask(
            request_id=request_id,
            text="hello",
            cancel_event=threading.Event(),
        )

    def test_worker_processes_task_and_publishes_events(self):
        queues = RuntimeQueues()
        shutdown_event = threading.Event()
        processor = Mock(spec=SpeechTaskProcessor)
        task = self._task("request-1")
        processor.process.return_value = WorkerEvent(
            worker=WorkerName.SPEECH,
            event_type=WorkerEventType.COMPLETED,
            request_id=task.request_id,
        )
        worker = SpeechWorker(
            queues.speech,
            queues.events,
            processor,
            shutdown_event,
        )

        worker.start()

        try:
            queues.speech.put(task)
            started = queues.events.get(timeout=1.0)
            completed = queues.events.get(timeout=1.0)
        finally:
            worker.stop()

        self.assertEqual(started.event_type, WorkerEventType.STARTED)
        self.assertEqual(started.request_id, task.request_id)
        self.assertEqual(completed.event_type, WorkerEventType.COMPLETED)
        processor.process.assert_called_once_with(task)
        processor.stop.assert_called_once()
        self.assertFalse(worker.is_alive())

    def test_worker_survives_processor_error(self):
        queues = RuntimeQueues()
        shutdown_event = threading.Event()
        processor = Mock(spec=SpeechTaskProcessor)
        first_task = self._task("request-1")
        second_task = self._task("request-2")

        def process(task):
            if task is first_task:
                raise RuntimeError("processor failed")

            return WorkerEvent(
                worker=WorkerName.SPEECH,
                event_type=WorkerEventType.COMPLETED,
                request_id=task.request_id,
            )

        processor.process.side_effect = process
        worker = SpeechWorker(
            queues.speech,
            queues.events,
            processor,
            shutdown_event,
        )
        worker.start()

        try:
            queues.speech.put(first_task)
            queues.speech.put(second_task)
            events = [
                queues.events.get(timeout=1.0)
                for _ in range(4)
            ]
        finally:
            worker.stop()

        self.assertEqual(
            [event.event_type for event in events],
            [
                WorkerEventType.STARTED,
                WorkerEventType.FAILED,
                WorkerEventType.STARTED,
                WorkerEventType.COMPLETED,
            ],
        )
        self.assertEqual(events[1].error, "processor failed")
        self.assertEqual(processor.process.call_count, 2)

    def test_worker_processes_next_task_after_cancellation(self):
        queues = RuntimeQueues()
        shutdown_event = threading.Event()
        processor = Mock(spec=SpeechTaskProcessor)
        cancelled_task = self._task("request-1")
        next_task = self._task("request-2")
        processor.process.side_effect = [
            WorkerEvent(
                worker=WorkerName.SPEECH,
                event_type=WorkerEventType.CANCELLED,
                request_id=cancelled_task.request_id,
            ),
            WorkerEvent(
                worker=WorkerName.SPEECH,
                event_type=WorkerEventType.COMPLETED,
                request_id=next_task.request_id,
            ),
        ]
        worker = SpeechWorker(
            queues.speech,
            queues.events,
            processor,
            shutdown_event,
        )
        worker.start()

        try:
            queues.speech.put(cancelled_task)
            queues.speech.put(next_task)
            events = [
                queues.events.get(timeout=1.0)
                for _ in range(4)
            ]
        finally:
            worker.stop()

        self.assertEqual(
            [event.event_type for event in events],
            [
                WorkerEventType.STARTED,
                WorkerEventType.CANCELLED,
                WorkerEventType.STARTED,
                WorkerEventType.COMPLETED,
            ],
        )
        self.assertEqual(processor.process.call_count, 2)
        self.assertFalse(worker.is_alive())

    def test_stream_cancellation_does_not_leak_into_next_request(self):
        queues = RuntimeQueues()
        shutdown_event = threading.Event()
        provider = RecordingStreamingProvider()
        fallback = Mock(name="fallback_provider")
        fallback.name = "fallback"
        fallback.is_available.return_value = True
        controller = SpeechPlaybackController()
        processor = SpeechTaskProcessor(
            lambda: [provider, fallback],
            controller,
            normalise_text=lambda text: text,
            segment_text=lambda text: [text],
        )
        worker = SpeechWorker(
            queues.speech,
            queues.events,
            processor,
            shutdown_event,
        )
        cancelled_task = SpeechTask(
            request_id="request-1",
            text="cancel this response",
            cancel_event=threading.Event(),
        )
        next_task = SpeechTask(
            request_id="request-2",
            text="play this response",
            cancel_event=threading.Event(),
        )
        worker.start()

        try:
            queues.speech.put(cancelled_task)
            first_started = queues.events.get(timeout=1.0)
            self.assertEqual(first_started.event_type, WorkerEventType.STARTED)

            self.assertTrue(provider.session_created.wait(timeout=1.0))
            first_session = provider.sessions[0]
            self.assertTrue(first_session.first_frame_played.wait(timeout=1.0))
            cancel_event = queues.speech.cancel_current()
            self.assertIs(cancel_event, cancelled_task.cancel_event)
            self.assertTrue(
                processor.stop(expected_cancel_event=cancel_event)
            )
            cancelled = queues.events.get(timeout=1.0)

            queues.speech.put(next_task)
            second_started = queues.events.get(timeout=1.0)
            completed = queues.events.get(timeout=1.0)
        finally:
            worker.stop(timeout=1.0)

        self.assertEqual(cancelled.event_type, WorkerEventType.CANCELLED)
        self.assertEqual(second_started.event_type, WorkerEventType.STARTED)
        self.assertEqual(completed.event_type, WorkerEventType.COMPLETED)
        self.assertEqual(len(provider.sessions), 2)
        self.assertEqual(
            provider.played_frames,
            [
                ("cancel this response", b"first"),
                ("play this response", b"first"),
                ("play this response", b"second"),
            ],
        )
        fallback.create_session.assert_not_called()
        self.assertFalse(controller.is_playing())
        self.assertFalse(worker.is_alive())

    def test_worker_cancels_task_during_provider_resolution(self):
        queues = RuntimeQueues()
        shutdown_event = threading.Event()
        resolver_entered = threading.Event()
        release_resolver = threading.Event()
        provider = FakeProvider("available")
        controller = Mock(spec=SpeechPlaybackController)
        controller.play.return_value = True
        task = self._task("request-1")

        def resolve_providers():
            resolver_entered.set()
            release_resolver.wait(timeout=1.0)
            return [provider]

        processor = SpeechTaskProcessor(
            resolve_providers,
            controller,
        )
        worker = SpeechWorker(
            queues.speech,
            queues.events,
            processor,
            shutdown_event,
        )
        worker.start()

        try:
            queues.speech.put(task)
            started = queues.events.get(timeout=1.0)
            self.assertTrue(resolver_entered.wait(timeout=1.0))

            cancelled_outstanding = queues.speech.cancel_current()
            release_resolver.set()
            cancelled = queues.events.get(timeout=1.0)
        finally:
            release_resolver.set()
            worker.stop()

        self.assertTrue(cancelled_outstanding)
        self.assertTrue(task.cancel_event.is_set())
        self.assertEqual(started.event_type, WorkerEventType.STARTED)
        self.assertEqual(cancelled.event_type, WorkerEventType.CANCELLED)
        controller.play.assert_not_called()
        self.assertFalse(worker.is_alive())

    def test_worker_stop_cancels_all_outstanding_tasks(self):
        queues = RuntimeQueues()
        shutdown_event = threading.Event()
        processing_started = threading.Event()
        processor = Mock(spec=SpeechTaskProcessor)
        current_task = self._task("request-1")
        queued_task = self._task("request-2")

        def process(task):
            processing_started.set()
            task.cancel_event.wait(timeout=1.0)
            return WorkerEvent(
                worker=WorkerName.SPEECH,
                event_type=WorkerEventType.CANCELLED,
                request_id=task.request_id,
            )

        processor.process.side_effect = process
        worker = SpeechWorker(
            queues.speech,
            queues.events,
            processor,
            shutdown_event,
        )
        worker.start()

        try:
            queues.speech.put(current_task)
            queues.speech.put(queued_task)
            self.assertTrue(processing_started.wait(timeout=1.0))
            worker.stop(timeout=1.0)
        finally:
            if worker.is_alive():
                worker.stop(timeout=1.0)

        self.assertTrue(current_task.cancel_event.is_set())
        self.assertTrue(queued_task.cancel_event.is_set())
        processor.stop.assert_called_once_with()
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
