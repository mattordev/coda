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
        processor = SpeechTaskProcessor(
            resolve_providers,
            controller,
        )

        event = processor.process(task)

        self.assertEqual(event.event_type, WorkerEventType.CANCELLED)
        self.assertEqual(event.worker, WorkerName.SPEECH)
        self.assertEqual(event.request_id, task.request_id)
        resolve_providers.assert_not_called()
        controller.play.assert_not_called()

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
