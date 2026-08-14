import time
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

    def test_restart_waits_for_timed_out_worker_to_exit(self):
        shutdown_event = Event()
        first_started = Event()
        release_first = Event()
        second_started = Event()
        starts = []

        def target(stop_event):
            starts.append("started")

            if len(starts) == 1:
                first_started.set()
                release_first.wait(timeout=1.0)
                return

            second_started.set()
            stop_event.wait(timeout=1.0)

        worker = VoiceInputWorker(target, shutdown_event)
        worker.start()
        self.assertTrue(first_started.wait(timeout=1.0))

        try:
            worker.stop(timeout=0.01)
            self.assertTrue(worker.is_alive())

            worker.start()
            release_first.set()

            self.assertTrue(second_started.wait(timeout=1.0))
            self.assertEqual(starts, ["started", "started"])
        finally:
            release_first.set()
            worker.stop(timeout=1.0)

        self.assertFalse(worker.is_alive())

    def test_stop_cancels_restart_requested_during_shutdown(self):
        shutdown_event = Event()
        first_started = Event()
        first_finished = Event()
        release_first = Event()
        second_started = Event()

        def target(_stop_event):
            if not first_started.is_set():
                first_started.set()
                release_first.wait(timeout=1.0)
                first_finished.set()
                return

            second_started.set()

        worker = VoiceInputWorker(target, shutdown_event)
        worker.start()
        self.assertTrue(first_started.wait(timeout=1.0))

        worker.stop(timeout=0.01)
        worker.start()
        worker.stop(timeout=0.01)
        release_first.set()

        self.assertTrue(first_finished.wait(timeout=1.0))
        deadline = time.monotonic() + 1.0
        while worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertFalse(worker.is_alive())
        self.assertFalse(second_started.is_set())


if __name__ == "__main__":
    unittest.main()
