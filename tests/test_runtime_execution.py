import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import coda_runtime
from runtime.active_request import ActiveRequestState
from runtime.messages import (
    ExecutionResult,
    InputSource,
    RuntimeRequest,
    SpeechTask,
    WorkerEvent,
    WorkerEventType,
    WorkerName,
)
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

    def test_execution_loop_queues_manual_response_without_voice_follow_up(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        request = RuntimeRequest(
            message="tell me something",
            source=InputSource.MANUAL,
        )
        command_result = SimpleNamespace(
            handled=True,
            response_text="General response",
            open_follow_up=True,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime.command,
                "run",
                return_value=command_result,
            ),
            patch.object(
                coda_runtime.dashboard_state,
                "record_ai_response",
            ) as record_ai_response,
            patch("builtins.print"),
        ):
            worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.requests.put(request)
                result = queues.events.get(timeout=1.0)
                speech_task = queues.speech.get(timeout=1.0)
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        self.assertEqual(result.response_text, "General response")
        self.assertFalse(result.open_follow_up)
        self.assertEqual(speech_task.request_id, request.request_id)
        self.assertEqual(speech_task.text, "General response")
        self.assertIs(speech_task.cancel_event, request.cancel_event)
        self.assertFalse(speech_task.open_follow_up)
        record_ai_response.assert_called_once_with(
            "General response",
            source="tts",
        )

    def test_execution_loop_preserves_voice_follow_up_on_speech_task(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        request = RuntimeRequest(
            message="tell me something",
            source=InputSource.VOICE,
        )
        command_result = SimpleNamespace(
            handled=True,
            response_text="Voice response",
            open_follow_up=True,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime.command,
                "run",
                return_value=command_result,
            ),
            patch.object(
                coda_runtime.dashboard_state,
                "record_ai_response",
            ),
            patch("builtins.print"),
        ):
            worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.requests.put(request)
                result = queues.events.get(timeout=1.0)
                speech_task = queues.speech.get(timeout=1.0)
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        self.assertTrue(result.open_follow_up)
        self.assertTrue(speech_task.open_follow_up)

    def test_execution_loop_tracks_active_request(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
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
        observed_request_ids = []

        def run_command(*_args, **_kwargs):
            observed_request_ids.append(
                active_state.active_request_id()
            )
            return command_result

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
            patch.object(
                coda_runtime.command,
                "run",
                side_effect=run_command,
            ),
        ):
            worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.requests.put(request)
                queues.events.get(timeout=1.0)
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        self.assertEqual(
            observed_request_ids,
            [request.request_id],
        )
        self.assertIsNone(
            active_state.active_request_id()
        )

    def test_cancel_active_request_signals_request(self):
        active_state = ActiveRequestState()
        speech_processor = Mock()
        speech_processor.stop.return_value = False
        request = RuntimeRequest(
            message="long-running request",
            source=InputSource.VOICE,
        )
        active_state.activate(request)

        with (
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
            patch.object(
                coda_runtime,
                "speech_processor",
                speech_processor,
            ),
            patch.object(
                coda_runtime.runtime_state,
                "is_debug_enabled",
                return_value=False,
            ),
            patch("builtins.print") as print_output,
        ):
            cancelled_id = coda_runtime.cancel_active_request()

        self.assertEqual(cancelled_id, request.request_id)
        self.assertTrue(request.cancel_event.is_set())
        speech_processor.stop.assert_not_called()
        print_output.assert_called_once_with(
            "[RUNTIME] Cancellation requested",
            flush=True,
        )

    def test_cancel_active_request_is_safe_when_idle(self):
        active_state = ActiveRequestState()
        speech_processor = Mock()
        speech_processor.stop.return_value = False

        with (
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
            patch.object(
                coda_runtime,
                "speech_processor",
                speech_processor,
            ),
            patch("builtins.print") as print_output,
        ):
            cancelled_id = coda_runtime.cancel_active_request()

        self.assertIsNone(cancelled_id)
        speech_processor.stop.assert_not_called()
        print_output.assert_called_once_with(
            "[RUNTIME] No active request to cancel",
            flush=True,
        )

    def test_cancel_active_request_stops_speech_when_execution_is_idle(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        speech_processor = Mock()
        speech_processor.stop.return_value = True
        speech_task = SpeechTask(
            request_id="speech-request",
            text="response",
            cancel_event=threading.Event(),
        )
        queues.speech.put(speech_task)

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
            patch.object(
                coda_runtime,
                "speech_processor",
                speech_processor,
            ),
            patch.object(
                coda_runtime.runtime_state,
                "is_debug_enabled",
                return_value=True,
            ),
            patch("builtins.print") as print_output,
        ):
            cancelled_id = coda_runtime.cancel_active_request()

        self.assertIsNone(cancelled_id)
        self.assertTrue(speech_task.cancel_event.is_set())
        speech_processor.stop.assert_called_once_with(
            expected_cancel_event=speech_task.cancel_event,
        )
        print_output.assert_called_once_with(
            "[RUNTIME] Speech cancellation requested",
            flush=True,
        )

    def test_cancel_active_request_stops_execution_and_speech(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        speech_processor = Mock()
        speech_processor.stop.return_value = True
        request = RuntimeRequest(
            message="new request",
            source=InputSource.VOICE,
        )
        active_state.activate(request)
        speech_task = SpeechTask(
            request_id=request.request_id,
            text="response",
            cancel_event=request.cancel_event,
        )
        queues.speech.put(speech_task)

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
            patch.object(
                coda_runtime,
                "speech_processor",
                speech_processor,
            ),
            patch.object(
                coda_runtime.runtime_state,
                "is_debug_enabled",
                return_value=False,
            ),
            patch("builtins.print") as print_output,
        ):
            cancelled_id = coda_runtime.cancel_active_request()

        self.assertEqual(cancelled_id, request.request_id)
        self.assertTrue(request.cancel_event.is_set())
        speech_processor.stop.assert_called_once_with(
            expected_cancel_event=request.cancel_event,
        )
        print_output.assert_called_once_with(
            "[RUNTIME] Cancellation requested",
            flush=True,
        )

    def test_cancel_after_speech_enqueue_before_worker_pickup(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        speech_processor = Mock()
        speech_processor.stop.return_value = False
        stop_event = threading.Event()
        request = RuntimeRequest(
            message="tell me something",
            source=InputSource.VOICE,
        )
        command_result = SimpleNamespace(
            handled=True,
            response_text="Queued response",
            open_follow_up=True,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
            patch.object(
                coda_runtime,
                "speech_processor",
                speech_processor,
            ),
            patch.object(
                coda_runtime.command,
                "run",
                return_value=command_result,
            ),
            patch.object(
                coda_runtime.dashboard_state,
                "record_ai_response",
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
                cancelled_id = coda_runtime.cancel_active_request()
                speech_task = queues.speech.get(timeout=1.0)
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        self.assertEqual(result.request_id, request.request_id)
        self.assertIsNone(active_state.active_request_id())
        self.assertIsNone(cancelled_id)
        self.assertTrue(speech_task.cancel_event.is_set())
        speech_processor.stop.assert_called_once_with(
            expected_cancel_event=request.cancel_event,
        )
        print_output.assert_any_call(
            "[RUNTIME] Speech cancellation requested",
            flush=True,
        )

    def test_normal_submission_does_not_cancel_active_request(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        active_request = RuntimeRequest(
            message="active request",
            source=InputSource.VOICE,
        )
        submitted_request = RuntimeRequest(
            message="queued request",
            source=InputSource.VOICE,
        )
        active_state.activate(active_request)

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
        ):
            cancelled_id = coda_runtime.submit_runtime_request(
                submitted_request
            )

        self.assertIsNone(cancelled_id)
        self.assertFalse(active_request.cancel_event.is_set())
        self.assertIs(
            queues.requests.get(timeout=0.01),
            submitted_request,
        )

    def test_speech_submission_uses_active_request_context(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        request = RuntimeRequest(
            message="connected",
            source=InputSource.VOICE,
        )
        active_state.activate(request)

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
        ):
            submitted = coda_runtime.submit_speech_response("hello")

        task = queues.speech.get(timeout=0.01)
        self.assertTrue(submitted)
        self.assertEqual(task.request_id, request.request_id)
        self.assertIs(task.cancel_event, request.cancel_event)

    def test_cancelled_request_cannot_submit_speech(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        request = RuntimeRequest(
            message="connected",
            source=InputSource.VOICE,
        )
        request.cancel_event.set()
        active_state.activate(request)

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
        ):
            submitted = coda_runtime.submit_speech_response("too late")

        self.assertFalse(submitted)
        self.assertIsNone(queues.speech.get(timeout=0.01))

    def test_manual_speech_submission_gets_standalone_context(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
        ):
            submitted = coda_runtime.submit_speech_response("manual reply")

        task = queues.speech.get(timeout=0.01)
        self.assertTrue(submitted)
        self.assertEqual(task.text, "manual reply")
        self.assertTrue(task.request_id)
        self.assertFalse(task.cancel_event.is_set())

    def test_replacement_submission_cancels_active_request(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        active_request = RuntimeRequest(
            message="active request",
            source=InputSource.VOICE,
        )
        replacement_request = RuntimeRequest(
            message="replacement request",
            source=InputSource.VOICE,
            replace_active=True,
        )
        active_state.activate(active_request)

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
        ):
            cancelled_id = coda_runtime.submit_runtime_request(
                replacement_request
            )

        self.assertEqual(cancelled_id, active_request.request_id)
        self.assertTrue(active_request.cancel_event.is_set())
        self.assertIs(
            queues.requests.get(timeout=0.01),
            replacement_request,
        )

    def test_cancellation_during_execution_does_not_affect_next_request(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        stop_event = threading.Event()
        command_started = threading.Event()
        allow_command_to_finish = threading.Event()

        first_request = RuntimeRequest(
            message="slow request",
            source=InputSource.VOICE,
        )
        second_request = RuntimeRequest(
            message="next request",
            source=InputSource.VOICE,
        )

        cancelled_command_result = SimpleNamespace(
            handled=True,
            response_text="stale response",
            open_follow_up=True,
        )
        next_command_result = SimpleNamespace(
            handled=True,
            response_text=None,
            open_follow_up=False,
        )

        def run_command(message, *_args, **_kwargs):
            if message == first_request.message:
                command_started.set()
                allow_command_to_finish.wait(timeout=1.0)
                return cancelled_command_result

            return next_command_result

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(
                coda_runtime,
                "active_request_state",
                active_state,
            ),
            patch.object(
                coda_runtime.command,
                "run",
                side_effect=run_command,
            ),
            patch("builtins.print"),
        ):
            worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.requests.put(first_request)
                self.assertTrue(
                    command_started.wait(timeout=1.0)
                )

                cancelled_id = active_state.cancel_active()
                queues.requests.put(second_request)
                allow_command_to_finish.set()

                first_result = queues.events.get(timeout=1.0)
                second_result = queues.events.get(timeout=1.0)
            finally:
                stop_event.set()
                allow_command_to_finish.set()
                worker.join(timeout=1.0)

        self.assertEqual(
            cancelled_id,
            first_request.request_id,
        )
        self.assertTrue(first_result.cancelled)
        self.assertFalse(first_result.handled)
        self.assertIsNone(first_result.response_text)
        self.assertFalse(first_result.open_follow_up)
        self.assertIsNone(queues.speech.get(timeout=0.01))

        self.assertEqual(
            second_result.request_id,
            second_request.request_id,
        )
        self.assertTrue(second_result.handled)
        self.assertFalse(second_result.cancelled)
        self.assertIsNone(active_state.active_request_id())

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
            submit_request=coda_runtime.submit_runtime_request,
            follow_up_state=coda_runtime.follow_up_state,
            cancel_active_request=coda_runtime.cancel_active_request,
            is_speech_playing=(
                coda_runtime.speech_playback_controller.is_playing
            ),
        )

    def test_manual_input_uses_runtime_request_queue(self):
        with (
            patch.object(coda_runtime, "commands", {}),
            patch.object(coda_runtime, "wakewords", []),
            patch.object(coda_runtime, "manual_assisstant_input", False),
            patch.object(
                coda_runtime,
                "_apply_cli_microphone_flags",
                return_value=True,
            ),
            patch.object(
                coda_runtime,
                "check_update_available",
                return_value=False,
            ),
            patch.object(
                coda_runtime,
                "load_commands",
                return_value={},
            ),
            patch.object(
                coda_runtime,
                "load_wakewords",
                return_value=["coda"],
            ),
            patch.object(coda_runtime.command, "configure_intent_router"),
            patch.object(coda_runtime, "start_heartbeat_thread"),
            patch.object(coda_runtime, "start_execution_thread"),
            patch.object(coda_runtime, "start_event_thread"),
            patch.object(coda_runtime, "start_speech_thread"),
            patch.object(coda_runtime.speech, "configure_speech_submitter"),
            patch.object(
                coda_runtime,
                "submit_runtime_request",
            ) as submit_request,
            patch.object(coda_runtime.command, "run") as run_command,
            patch(
                "builtins.input",
                side_effect=["CODA say CPU Usage", "quit"],
            ),
            patch("builtins.print"),
        ):
            coda_runtime._run_runtime()

        run_command.assert_not_called()
        submit_request.assert_called_once()
        submitted_request = submit_request.call_args.args[0]
        self.assertIsInstance(submitted_request, RuntimeRequest)
        self.assertEqual(submitted_request.message, "say CPU Usage")
        self.assertEqual(submitted_request.source, InputSource.MANUAL)
        self.assertFalse(submitted_request.replace_active)
        
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

    def test_speech_thread_uses_speech_worker_lifecycle(self):
        speech_worker = Mock()

        with patch.object(
            coda_runtime,
            "speech_worker",
            speech_worker,
        ):
            coda_runtime.start_speech_thread()
            coda_runtime.stop_speech_thread(timeout_seconds=2.5)

        speech_worker.start.assert_called_once_with()
        speech_worker.stop.assert_called_once_with(timeout=2.5)

    def test_speech_started_event_closes_follow_up(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        follow_up = Mock()
        event = WorkerEvent(
            worker=WorkerName.SPEECH,
            event_type=WorkerEventType.STARTED,
            request_id="request-1",
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime, "follow_up_state", follow_up),
        ):
            worker = threading.Thread(
                target=coda_runtime._event_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.events.put(event)
                queues.events.join()
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        follow_up.close.assert_called_once_with()
        follow_up.open.assert_not_called()

    def test_completed_speech_opens_requested_follow_up(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        follow_up = Mock()
        event = WorkerEvent(
            worker=WorkerName.SPEECH,
            event_type=WorkerEventType.COMPLETED,
            request_id="request-1",
            open_follow_up=True,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime, "follow_up_state", follow_up),
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
                queues.events.put(event)
                queues.events.join()
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        follow_up.open.assert_called_once_with(10.0)
        follow_up.close.assert_not_called()

    def test_stale_speech_completion_does_not_open_follow_up(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        follow_up = Mock()
        stale_event = WorkerEvent(
            worker=WorkerName.SPEECH,
            event_type=WorkerEventType.COMPLETED,
            request_id="request-a",
            open_follow_up=True,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime, "follow_up_state", follow_up),
            patch.object(coda_runtime, "latest_request_id", "request-b"),
        ):
            worker = threading.Thread(
                target=coda_runtime._event_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.events.put(stale_event)
                queues.events.join()
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        follow_up.open.assert_not_called()

    def test_cancelled_speech_keeps_follow_up_closed(self):
        queues = RuntimeQueues()
        stop_event = threading.Event()
        follow_up = Mock()
        event = WorkerEvent(
            worker=WorkerName.SPEECH,
            event_type=WorkerEventType.CANCELLED,
            request_id="request-1",
            open_follow_up=True,
        )

        with (
            patch.object(coda_runtime, "runtime_queues", queues),
            patch.object(coda_runtime, "follow_up_state", follow_up),
        ):
            worker = threading.Thread(
                target=coda_runtime._event_loop,
                args=(stop_event,),
            )
            worker.start()

            try:
                queues.events.put(event)
                queues.events.join()
            finally:
                stop_event.set()
                worker.join(timeout=1.0)

        follow_up.close.assert_called_once_with()
        follow_up.open.assert_not_called()
    
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
