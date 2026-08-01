import unittest

from runtime.messages import InputSource
from runtime.runtime_queue import RuntimeQueue
from utils.voice_recognizer import _queue_runtime_request


class VoiceRecognizerTests(unittest.TestCase):

    def test_queues_voice_runtime_request(self):
        request_queue = RuntimeQueue()

        request = _queue_runtime_request(
            "where is the station",
            request_queue,
        )
        queued_request = request_queue.get(timeout=0.1)

        self.assertIs(queued_request, request)
        self.assertEqual(queued_request.message, "where is the station")
        self.assertEqual(queued_request.source, InputSource.VOICE)


if __name__ == "__main__":
    unittest.main()