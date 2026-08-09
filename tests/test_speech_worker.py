import threading
import unittest
from unittest.mock import Mock

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

    def test_provider_error_falls_back_to_next_provider(self):
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


if __name__ == "__main__":
    unittest.main()
