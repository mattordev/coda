import threading
import unittest

from runtime.runtime_queue import RuntimeQueue, RuntimeQueues


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


if __name__ == "__main__":
    unittest.main()