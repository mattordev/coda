"""An optional dashboard failure cannot turn valid answers into failed work."""

from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import coda_runtime
from runtime.messages import InputSource, RuntimeRequest
from runtime.runtime_queue import RuntimeQueues
from utils import speak_response


class DashboardSpeechIsolationTests(unittest.TestCase):
    def test_dashboard_denial_keeps_final_runtime_answer_and_speech(self):
        queues, stop = RuntimeQueues(), Event()
        request = RuntimeRequest('test response', InputSource.MANUAL)
        response = SimpleNamespace(handled=True, response_text='A valid answer.', open_follow_up=False)
        with (patch.object(coda_runtime, 'runtime_queues', queues),
              patch.object(coda_runtime.command, 'run', return_value=response),
              patch.object(coda_runtime.dashboard_state, 'record_ai_response', side_effect=PermissionError('locked')),
              patch('builtins.print')):
            worker = Thread(target=coda_runtime._execution_loop, args=(stop,))
            worker.start()
            try:
                queues.requests.put(request)
                self.assertTrue(request.execution_complete.wait(3))
                result = queues.events.get(timeout=0)
                task = queues.speech.get(timeout=0)
            finally:
                stop.set()
                worker.join(2)
        self.assertTrue(result.handled)
        self.assertIsNone(result.error)
        self.assertEqual(task.text, 'A valid answer.')
        self.assertFalse(worker.is_alive())

    def test_native_speech_submission_survives_dashboard_write_denial(self):
        submit = Mock(return_value=True)
        with (patch.object(speak_response, '_speech_submitter', submit),
              patch.object(speak_response.dashboard_state, 'record_ai_response', side_effect=PermissionError('locked')),
              patch('builtins.print')):
            self.assertTrue(speak_response.speak_response('The time is twelve.'))
        submit.assert_called_once_with('The time is twelve.')
