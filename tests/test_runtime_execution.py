import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import coda_runtime
from runtime.messages import ExecutionResult, InputSource, RuntimeRequest
from runtime.runtime_queue import RuntimeQueues
from runtime.follow_up import FollowUpState

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
            patch.object(
                coda_runtime.time,
                "time",
                return_value=request.created_at + 0.25,
            ),
            patch.object(
                coda_runtime.time,
                "perf_counter",
                side_effect=[100.0, 102.5],
            ),
            patch.object(
                coda_runtime.runtime_state,
                "is_debug_enabled",
                return_value=True,
            ),
            patch("builtins.print") as print_output,
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

        request_label = f"voice request {request.request_id[:8]}"

        print_output.assert_any_call(
            f"[RUNTIME] Processing {request_label} "
            f"after 0.25s queued",
            flush=True,
        )
        print_output.assert_any_call(
            f"[RUNTIME] Completed {request_label} in 2.50s",
            flush=True,
        )

    def test_execution_loop_reports_unhandled_request(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        request = RuntimeRequest(
            message="unknown request",
            source=InputSource.VOICE,
        )
        command_result = SimpleNamespace(
            handled=False,
            response_text=None,
            open_follow_up=False,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime.command,
                "run",
                return_value=command_result,
            ),
            patch.object(
                coda_runtime.time,
                "perf_counter",
                side_effect=[100.0, 100.5],
            ),
            patch.object(
                coda_runtime.runtime_state,
                "is_debug_enabled",
                return_value=False,
            ),
            patch("builtins.print") as print_output,
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

        self.assertFalse(result.handled)
        print_output.assert_any_call(
            "[RUNTIME] Processing request",
            flush=True,
        )
        print_output.assert_any_call(
            "[RUNTIME] Request not handled",
            flush=True,
        )
        
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
            patch.object(
                coda_runtime.time,
                "perf_counter",
                side_effect=[100.0, 100.0],
            ),
            patch.object(
                coda_runtime.runtime_state,
                "is_debug_enabled",
                return_value=True,
            ),
            patch("builtins.print") as print_output,
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

        request_label = f"voice request {request.request_id[:8]}"
        print_output.assert_any_call(
            f"[RUNTIME] Cancelled {request_label} in 0.00s",
            flush=True,
        )
    
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
            patch.object(
                coda_runtime.time,
                "perf_counter",
                side_effect=[100.0, 101.0, 200.0, 202.0],
            ),
            patch.object(
                coda_runtime.runtime_state,
                "is_debug_enabled",
                return_value=True,
            ),
            patch("builtins.print") as print_output,
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

        first_request_label = (
            f"voice request {first_request.request_id[:8]}"
        )
        print_output.assert_any_call(
            f"[RUNTIME] Failed {first_request_label} in 1.00s",
            flush=True,
        )
        
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
            follow_up_state=coda_runtime.follow_up_state,
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
    
    def test_event_loop_opens_follow_up_from_execution_result(self):
        queues = RuntimeQueues()
        follow_up_state = FollowUpState()
        stop_event = threading.Event()
        opened = threading.Event()
        real_open = follow_up_state.open

        def open_follow_up(duration_seconds):
            real_open(duration_seconds)
            opened.set()

        execution_result = ExecutionResult(
            request_id="request-1",
            handled=True,
            open_follow_up=True,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime, "follow_up_state", follow_up_state),
            patch.object(
                follow_up_state,
                "open",
                side_effect=open_follow_up,
            ),
            patch.object(
                coda_runtime.voice_recognizer,
                "get_follow_up_timeout_seconds",
                return_value=10.0,
            ),
        ):
            worker = threading.Thread(
                target=coda_runtime._event_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.events.put(execution_result)
                self.assertTrue(opened.wait(timeout=1.0))
                self.assertTrue(follow_up_state.is_active())
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        
    def test_event_loop_closes_follow_up_for_normal_result(self):
        queues = RuntimeQueues()
        follow_up_state = FollowUpState()
        stop_event = threading.Event()
        closed = threading.Event()
        real_close = follow_up_state.close

        follow_up_state.open(duration_seconds=10.0)

        def close_follow_up():
            real_close()
            closed.set()

        execution_result = ExecutionResult(
            request_id="request-1",
            handled=True,
            open_follow_up=False,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime, "follow_up_state", follow_up_state),
            patch.object(
                follow_up_state,
                "close",
                side_effect=close_follow_up,
            ),
        ):
            worker = threading.Thread(
                target=coda_runtime._event_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.events.put(execution_result)
                self.assertTrue(closed.wait(timeout=1.0))
                self.assertFalse(follow_up_state.is_active())
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        
    def test_event_thread_starts_once_and_stops(self):
        stop_event = threading.Event()
        started = threading.Event()

        def event_target(received_stop_event):
            started.set()
            received_stop_event.wait()

        with (
            patch.object(coda_runtime, "event_stop_event", stop_event),
            patch.object(coda_runtime, "event_thread", None),
            patch.object(coda_runtime, "_event_loop", event_target),
        ):
            coda_runtime.start_event_thread()
            self.assertTrue(started.wait(timeout=1.0))

            first_thread = coda_runtime.event_thread
            coda_runtime.start_event_thread()

            self.assertIs(coda_runtime.event_thread, first_thread)
            self.assertTrue(first_thread.is_alive())

            coda_runtime.stop_event_thread()

            self.assertTrue(stop_event.is_set())
            self.assertFalse(first_thread.is_alive())
            
    def test_event_loop_closes_follow_up_for_error_result(self):
        queues = RuntimeQueues()
        follow_up_state = FollowUpState()
        stop_event = threading.Event()
        closed = threading.Event()
        real_close = follow_up_state.close

        follow_up_state.open(duration_seconds=10.0)

        def close_follow_up():
            real_close()
            closed.set()

        execution_result = ExecutionResult(
            request_id="request-1",
            handled=False,
            open_follow_up=True,
            error="provider failed",
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime, "follow_up_state", follow_up_state),
            patch.object(
                follow_up_state,
                "close",
                side_effect=close_follow_up,
            ),
            patch.object(
                coda_runtime.runtime_state,
                "is_debug_enabled",
                return_value=False,
            ),
        ):
            worker = threading.Thread(
                target=coda_runtime._event_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.events.put(execution_result)
                self.assertTrue(closed.wait(timeout=1.0))
                self.assertFalse(follow_up_state.is_active())
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
