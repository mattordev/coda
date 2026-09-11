import threading
import unittest
from unittest import mock

import coda_runtime
from runtime.active_request import ActiveRequestState
from runtime.messages import InputSource, RuntimeRequest
from runtime.runtime_queue import RuntimeQueues


class RuntimeShutdownTests(unittest.TestCase):
    def test_shutdown_cancels_active_request_and_stops_every_worker(self):
        shutdown_started = threading.Event()
        active_request_state = ActiveRequestState()
        request = RuntimeRequest(
            message="long-running request",
            source=InputSource.VOICE,
        )
        active_request_state.activate(request)

        with mock.patch.object(
            coda_runtime,
            "shutdown_started",
            shutdown_started,
        ), mock.patch.object(
            coda_runtime,
            "active_request_state",
            active_request_state,
        ), mock.patch.object(
            coda_runtime.follow_up_state,
            "close",
        ) as close_follow_up, mock.patch.object(
            coda_runtime,
            "stop_voice_thread",
        ) as stop_voice, mock.patch.object(
            coda_runtime,
            "stop_execution_thread",
        ) as stop_execution, mock.patch.object(
            coda_runtime.speech,
            "configure_speech_submitter",
        ) as configure_speech, mock.patch.object(
            coda_runtime,
            "stop_speech_thread",
        ) as stop_speech, mock.patch.object(
            coda_runtime.speech,
            "shutdown",
        ) as shutdown_speech, mock.patch.object(
            coda_runtime,
            "stop_event_thread",
        ) as stop_events, mock.patch.object(
            coda_runtime,
            "stop_heartbeat_thread",
        ) as stop_heartbeat:
            stopped = coda_runtime.shutdown_runtime(timeout_seconds=1.5)

        self.assertTrue(stopped)
        self.assertTrue(request.cancel_event.is_set())
        close_follow_up.assert_called_once_with()
        stop_voice.assert_called_once_with(timeout_seconds=1.5)
        stop_execution.assert_called_once_with(timeout_seconds=1.5)
        configure_speech.assert_called_once_with(None)
        stop_speech.assert_called_once_with(timeout_seconds=1.5)
        shutdown_speech.assert_called_once_with()
        stop_events.assert_called_once_with(timeout_seconds=1.5)
        stop_heartbeat.assert_called_once_with(timeout_seconds=1.5)

    def test_shutdown_is_idempotent(self):
        shutdown_started = threading.Event()

        with mock.patch.object(
            coda_runtime,
            "shutdown_started",
            shutdown_started,
        ), mock.patch.object(
            coda_runtime,
            "stop_voice_thread",
        ) as stop_voice, mock.patch.object(
            coda_runtime.active_request_state,
            "cancel_active",
        ), mock.patch.object(
            coda_runtime,
            "stop_execution_thread",
        ), mock.patch.object(
            coda_runtime.speech,
            "configure_speech_submitter",
        ), mock.patch.object(
            coda_runtime,
            "stop_speech_thread",
        ), mock.patch.object(
            coda_runtime.speech,
            "shutdown",
        ) as shutdown_speech, mock.patch.object(
            coda_runtime,
            "stop_event_thread",
        ), mock.patch.object(
            coda_runtime,
            "stop_heartbeat_thread",
        ):
            first_result = coda_runtime.shutdown_runtime()
            second_result = coda_runtime.shutdown_runtime()

        self.assertTrue(first_result)
        self.assertFalse(second_result)
        stop_voice.assert_called_once_with(timeout_seconds=4.0)
        shutdown_speech.assert_called_once_with()

    def test_shutdown_cancels_active_work_without_starting_pending_request(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        execution_stop = threading.Event()
        shutdown_started = threading.Event()
        command_started = threading.Event()
        active_request = RuntimeRequest(
            message="active request",
            source=InputSource.VOICE,
        )
        pending_request = RuntimeRequest(
            message="pending request",
            source=InputSource.VOICE,
        )

        def run_command(message, *_args, cancel_event=None, **_kwargs):
            self.assertEqual(message, active_request.message)
            command_started.set()
            self.assertTrue(cancel_event.wait(timeout=1.0))
            return mock.Mock(
                handled=True,
                response_text="cancelled response",
                open_follow_up=True,
            )

        with mock.patch.object(
            coda_runtime,
            "runtime_queues",
            queues,
        ), mock.patch.object(
            coda_runtime,
            "active_request_state",
            active_state,
        ), mock.patch.object(
            coda_runtime,
            "execution_stop_event",
            execution_stop,
        ), mock.patch.object(
            coda_runtime,
            "shutdown_started",
            shutdown_started,
        ), mock.patch.object(
            coda_runtime.command,
            "run",
            side_effect=run_command,
        ) as run, mock.patch.object(
            coda_runtime,
            "stop_voice_thread",
        ), mock.patch.object(
            coda_runtime.speech,
            "configure_speech_submitter",
        ), mock.patch.object(
            coda_runtime,
            "stop_speech_thread",
        ), mock.patch.object(
            coda_runtime.speech,
            "shutdown",
        ), mock.patch.object(
            coda_runtime,
            "stop_event_thread",
        ), mock.patch.object(
            coda_runtime,
            "stop_heartbeat_thread",
        ), mock.patch("builtins.print"):
            worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(execution_stop,),
            )

            with mock.patch.object(coda_runtime, "execution_thread", worker):
                worker.start()
                queues.requests.put(active_request)
                queues.requests.put(pending_request)
                self.assertTrue(command_started.wait(timeout=1.0))

                stopped = coda_runtime.shutdown_runtime(timeout_seconds=1.0)

            result = queues.events.get(timeout=1.0)

        self.assertTrue(stopped)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.request_id, active_request.request_id)
        self.assertTrue(active_request.cancel_event.is_set())
        self.assertIs(queues.requests.get(timeout=0.01), pending_request)
        run.assert_called_once()
        self.assertFalse(worker.is_alive())

    def test_main_shuts_down_cleanly_after_keyboard_interrupt(self):
        shutdown_started = threading.Event()

        with mock.patch.object(
            coda_runtime,
            "shutdown_started",
            shutdown_started,
        ), mock.patch.object(
            coda_runtime,
            "_run_runtime",
            side_effect=KeyboardInterrupt,
        ), mock.patch.object(
            coda_runtime,
            "shutdown_runtime",
        ) as shutdown, mock.patch("builtins.print") as print_output:
            coda_runtime.main()

        shutdown.assert_called_once_with()
        print_output.assert_called_once_with("Exiting C.O.D.A")

    def test_prepared_update_activates_only_after_shutdown(self):
        from utils import update_manager, update_bootstrap
        update = object()
        child = object()
        events = []
        with (
            mock.patch.object(coda_runtime, "_run_runtime", return_value=update),
            mock.patch.object(coda_runtime, "shutdown_runtime", side_effect=lambda: events.append("shutdown")),
            mock.patch.object(update_manager, "complete_update", side_effect=lambda value: events.append("activate") or child) as activate,
            mock.patch.object(update_bootstrap, "wait_for_process", return_value=0) as wait,
        ):
            self.assertEqual(coda_runtime.main(), 0)
        self.assertEqual(events, ["shutdown", "activate"])
        activate.assert_called_once_with(update)
        wait.assert_called_once_with(child)

    def test_prepared_update_does_not_start_old_runtime_workers(self):
        update = object()
        with (
            mock.patch.object(coda_runtime, "_apply_cli_microphone_flags", return_value=True),
            mock.patch.object(coda_runtime, "check_update_available", return_value=update),
            mock.patch.object(coda_runtime, "start_execution_thread") as execution,
            mock.patch.object(coda_runtime, "start_speech_thread") as speech,
            mock.patch.object(coda_runtime, "load_commands") as commands,
        ):
            self.assertIs(coda_runtime._run_runtime(), update)
        execution.assert_not_called()
        speech.assert_not_called()
        commands.assert_not_called()


if __name__ == "__main__":
    unittest.main()
