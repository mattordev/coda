import threading
import unittest

from runtime.messages import SpeechTask
from runtime.runtime_queue import (
    RuntimeQueue,
    RuntimeQueues,
    SpeechTaskQueue,
)


class RuntimeQueueTests(unittest.TestCase):
    def test_returns_messages_in_fifo_order(self):
        runtime_queue = RuntimeQueue[str]()
        runtime_queue.put("first")
        runtime_queue.put("second")

        self.assertEqual(runtime_queue.get(), "first")
        self.assertEqual(runtime_queue.get(), "second")

    def test_returns_none_after_timeout(self):
        runtime_queue = RuntimeQueue[str]()

        result = runtime_queue.get(timeout=0.01)

        self.assertIsNone(result)

    def test_passes_message_between_threads(self):
        runtime_queue = RuntimeQueue[str]()
        producer = threading.Thread(
            target=runtime_queue.put,
            args=("from worker",),
        )

        producer.start()
        result = runtime_queue.get(timeout=1.0)
        producer.join(timeout=1.0)

        self.assertFalse(producer.is_alive())
        self.assertEqual(result, "from worker")

    def test_runtime_queue_sets_are_independent(self):
        first = RuntimeQueues()
        second = RuntimeQueues()

        first.requests.put("request")

        self.assertEqual(first.requests.get(timeout=0.01), "request")
        self.assertIsNone(second.requests.get(timeout=0.01))
        
    def test_join_waits_until_message_is_processed(self):
        runtime_queue = RuntimeQueue[str]()
        runtime_queue.put("work")

        join_finished = threading.Event()

        def wait_for_queue():
            runtime_queue.join()
            join_finished.set()

        waiter = threading.Thread(target=wait_for_queue)
        waiter.start()

        self.assertFalse(join_finished.wait(timeout=0.01))

        message = runtime_queue.get(timeout=0.1)
        self.assertEqual(message, "work")

        runtime_queue.task_done()

        self.assertTrue(join_finished.wait(timeout=1.0))
        waiter.join(timeout=1.0)
        self.assertFalse(waiter.is_alive())

    def test_speech_queue_cancels_queued_and_dequeued_tasks(self):
        speech_queue = SpeechTaskQueue()
        resolving_task = SpeechTask(
            request_id="resolving-request",
            text="resolving response",
            cancel_event=threading.Event(),
        )
        queued_task = SpeechTask(
            request_id="queued-request",
            text="queued response",
            cancel_event=threading.Event(),
        )
        speech_queue.put(resolving_task)
        speech_queue.put(queued_task)

        claimed_task = speech_queue.get(timeout=0.01)
        self.assertIs(claimed_task, resolving_task)

        self.assertTrue(speech_queue.cancel_all())
        self.assertTrue(resolving_task.cancel_event.is_set())
        self.assertTrue(queued_task.cancel_event.is_set())

        speech_queue.complete(resolving_task)
        self.assertIs(speech_queue.get(timeout=0.01), queued_task)
        speech_queue.complete(queued_task)

    def test_speech_queue_cancels_only_oldest_outstanding_task(self):
        speech_queue = SpeechTaskQueue()
        current_task = SpeechTask(
            request_id="current-request",
            text="current response",
            cancel_event=threading.Event(),
        )
        later_task = SpeechTask(
            request_id="later-request",
            text="later response",
            cancel_event=threading.Event(),
        )
        speech_queue.put(current_task)
        speech_queue.put(later_task)

        self.assertIs(speech_queue.get(timeout=0.01), current_task)
        self.assertTrue(speech_queue.cancel_current())

        self.assertTrue(current_task.cancel_event.is_set())
        self.assertFalse(later_task.cancel_event.is_set())

        speech_queue.complete(current_task)
        self.assertIs(speech_queue.get(timeout=0.01), later_task)
        speech_queue.complete(later_task)

    def test_completed_speech_task_is_no_longer_cancellable(self):
        speech_queue = SpeechTaskQueue()
        task = SpeechTask(
            request_id="completed-request",
            text="completed response",
            cancel_event=threading.Event(),
        )
        speech_queue.put(task)

        self.assertIs(speech_queue.get(timeout=0.01), task)
        speech_queue.complete(task)

        self.assertFalse(speech_queue.cancel_all())
        self.assertFalse(task.cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
