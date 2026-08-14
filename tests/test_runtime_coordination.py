import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import coda_runtime
from runtime.active_request import ActiveRequestState
from runtime.messages import (
    ExecutionResult,
    InputSource,
    RuntimeRequest,
    WorkerEvent,
    WorkerEventType,
    WorkerName,
)
from runtime.runtime_queue import RuntimeQueues
from runtime.speech_worker import SpeechTaskProcessor, SpeechWorker


class RuntimeCoordinationTests(unittest.TestCase):
    def test_execution_and_speech_workers_preserve_request_identity_and_order(self):
        queues = RuntimeQueues()
        active_state = ActiveRequestState()
        execution_stop = threading.Event()
        speech_stop = threading.Event()
        request = RuntimeRequest(
            message="coordinated request",
            source=InputSource.VOICE,
        )
        command_result = SimpleNamespace(
            handled=True,
            response_text="coordinated response",
            open_follow_up=False,
        )
        processor = mock.Mock(spec=SpeechTaskProcessor)

        def process(task):
            return WorkerEvent(
                worker=WorkerName.SPEECH,
                event_type=WorkerEventType.COMPLETED,
                request_id=task.request_id,
            )

        processor.process.side_effect = process
        speech_worker = SpeechWorker(
            queues.speech,
            queues.events,
            processor,
            speech_stop,
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
            coda_runtime.command,
            "run",
            return_value=command_result,
        ), mock.patch.object(
            coda_runtime.dashboard_state,
            "record_ai_response",
        ), mock.patch("builtins.print"):
            execution_worker = threading.Thread(
                target=coda_runtime._execution_loop,
                args=(execution_stop,),
            )
            execution_worker.start()
            speech_worker.start()

            try:
                queues.requests.put(request)
                events = [
                    queues.events.get(timeout=1.0)
                    for _ in range(3)
                ]
            finally:
                execution_stop.set()
                execution_worker.join(timeout=1.0)
                speech_worker.stop(timeout=1.0)

        execution_result = next(
            event for event in events
            if isinstance(event, ExecutionResult)
        )
        speech_events = [
            event for event in events
            if isinstance(event, WorkerEvent)
        ]

        self.assertEqual(execution_result.request_id, request.request_id)
        self.assertTrue(execution_result.handled)
        self.assertEqual(
            [event.event_type for event in speech_events],
            [WorkerEventType.STARTED, WorkerEventType.COMPLETED],
        )
        self.assertTrue(
            all(event.request_id == request.request_id for event in speech_events)
        )

        processed_task = processor.process.call_args.args[0]
        self.assertIs(processed_task.cancel_event, request.cancel_event)
        self.assertFalse(execution_worker.is_alive())
        self.assertFalse(speech_worker.is_alive())


if __name__ == "__main__":
    unittest.main()
