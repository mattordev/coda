import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import coda_runtime
from runtime.messages import InputSource, RuntimeRequest
from runtime.runtime_queue import RuntimeQueues


class RuntimeExecutionTests(unittest.TestCase):

    def test_execution_loop_processes_queued_request(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        request = RuntimeRequest(
            message="connected",
            source=InputSource.VOICE,
        )
        command_result = SimpleNamespace(
            handled=True,
            response_text=None,
            open_follow_up=False,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime.command,
                "run",
                return_value=command_result,
            ) as run_command,
        ):
            worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.requests.put(request)
                result = queues.events.get(timeout=1.0)
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        run_command.assert_called_once()
        self.assertEqual(result.request_id, request.request_id)
        self.assertTrue(result.handled)
        self.assertFalse(worker.is_alive())
        
    def test_execution_loop_skips_cancelled_request(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        request = RuntimeRequest(
            message="connected",
            source=InputSource.VOICE,
        )
        request.cancel_event.set()

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime.command, "run") as run_command,
        ):
            worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.requests.put(request)
                result = queues.events.get(timeout=1.0)
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        run_command.assert_not_called()
        self.assertEqual(result.request_id, request.request_id)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.handled)
    
    def test_execution_loop_continues_after_command_error(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        first_request = RuntimeRequest(
            message="broken command",
            source=InputSource.VOICE,
        )
        second_request = RuntimeRequest(
            message="connected",
            source=InputSource.VOICE,
        )
        successful_result = SimpleNamespace(
            handled=True,
            response_text=None,
            open_follow_up=False,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime.command,
                "run",
                side_effect=[
                    RuntimeError("command failed"),
                    successful_result,
                ],
            ),
        ):
            worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.requests.put(first_request)
                queues.requests.put(second_request)

                first_result = queues.events.get(timeout=1.0)
                second_result = queues.events.get(timeout=1.0)

                self.assertTrue(worker.is_alive())
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        self.assertEqual(first_result.error, "command failed")
        self.assertFalse(first_result.handled)
        self.assertEqual(second_result.request_id, second_request.request_id)
        
    def test_voice_recognition_uses_runtime_request_queue(self):
        stop_event = threading.Event()
        queues = RuntimeQueues()

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime.voice_recognizer, "run") as run_voice,
            patch.object(coda_runtime.runtime_state, "set_input_mode"),
        ):
            coda_runtime.start_voice_recognition(stop_event)

        run_voice.assert_called_once_with(
            coda_runtime.wakewords,
            coda_runtime.commands,
            mode="normal",
            stop_event=stop_event,
            request_queue=queues.requests,
        )
        
    def test_execution_thread_starts_once_and_stops(self):
        stop_event = threading.Event()
        started = threading.Event()

        def execution_target(received_stop_event):
            started.set()
            received_stop_event.wait()

        with (
            patch.object(coda_runtime, "execution_stop_event", stop_event),
            patch.object(coda_runtime, "execution_thread", None),
            patch.object(coda_runtime, "_execution_loop", execution_target),
        ):
            coda_runtime.start_execution_thread()
            self.assertTrue(started.wait(timeout=1.0))

            first_thread = coda_runtime.execution_thread
            coda_runtime.start_execution_thread()

            self.assertIs(coda_runtime.execution_thread, first_thread)
            self.assertTrue(first_thread.is_alive())

            coda_runtime.stop_execution_thread()

            self.assertTrue(stop_event.is_set())
            self.assertFalse(first_thread.is_alive())


if __name__ == "__main__":
    unittest.main()