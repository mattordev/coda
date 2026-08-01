import unittest
from threading import Event

from runtime.voice_worker import VoiceInputWorker


class VoiceInputWorkerTests(unittest.TestCase):

    def test_starts_and_stops_worker(self):
        started = Event()
        shutdown_event = Event()

        def target(stop_event):
            started.set()
            stop_event.wait()

        worker = VoiceInputWorker(target, shutdown_event)

        worker.start()

        self.assertTrue(started.wait(timeout=1.0))
        self.assertTrue(worker.is_alive())

        worker.stop()

        self.assertTrue(shutdown_event.is_set())
        self.assertFalse(worker.is_alive())
    
    def test_start_does_not_create_duplicate_worker(self):
        started = Event()
        shutdown_event = Event()
        calls = []

        def target(stop_event):
            calls.append("started")
            started.set()
            stop_event.wait()

        worker = VoiceInputWorker(target, shutdown_event)

        worker.start()
        self.assertTrue(started.wait(timeout=1.0))

        worker.start()

        self.assertEqual(calls, ["started"])

        worker.stop()
        
    def test_worker_can_restart_after_stopping(self):
        shutdown_event = Event()
        starts = []

        def target(stop_event):
            starts.append("started")
            stop_event.wait()

        worker = VoiceInputWorker(target, shutdown_event)

        worker.start()
        worker.stop()

        worker.start()
        worker.stop()

        self.assertEqual(starts, ["started", "started"])
        
    def test_stop_is_safe_before_worker_starts(self):
        shutdown_event = Event()

        def target(stop_event):
            stop_event.wait()

        worker = VoiceInputWorker(target, shutdown_event)

        worker.stop()

        self.assertTrue(shutdown_event.is_set())
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()