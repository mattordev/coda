import unittest
from dataclasses import FrozenInstanceError

from runtime.messages import (
    ExecutionResult,
    InputSource,
    RuntimeRequest,
    SpeechTask,
    WorkerEvent,
    WorkerEventType,
    WorkerName,
)


class RuntimeMessageTests(unittest.TestCase):
    def test_requests_receive_unique_ids_and_events(self):
        first = RuntimeRequest("first", InputSource.MANUAL)
        second = RuntimeRequest("second", InputSource.VOICE)

        self.assertNotEqual(first.request_id, second.request_id)
        self.assertIsNot(first.cancel_event, second.cancel_event)

    def test_speech_task_shares_request_cancellation(self):
        request = RuntimeRequest("say hello", InputSource.MANUAL)
        speech = SpeechTask(
            request_id=request.request_id,
            text="Hello.",
            cancel_event=request.cancel_event,
        )

        request.cancel_event.set()

        self.assertTrue(speech.cancel_event.is_set())

    def test_execution_result_has_safe_defaults(self):
        result = ExecutionResult(
            request_id="request-id",
            handled=True,
        )

        self.assertIsNone(result.response_text)
        self.assertFalse(result.open_follow_up)
        self.assertFalse(result.cancelled)
        self.assertIsNone(result.error)

    def test_worker_event_records_failure_details(self):
        event = WorkerEvent(
            worker=WorkerName.SPEECH,
            event_type=WorkerEventType.FAILED,
            request_id="request-id",
            error="TTS unavailable",
        )

        self.assertEqual(event.worker, WorkerName.SPEECH)
        self.assertEqual(event.event_type, WorkerEventType.FAILED)
        self.assertEqual(event.error, "TTS unavailable")

    def test_runtime_messages_cannot_be_reassigned(self):
        request = RuntimeRequest("hello", InputSource.MANUAL)

        with self.assertRaises(FrozenInstanceError):
            request.message = "changed"


if __name__ == "__main__":
    unittest.main()