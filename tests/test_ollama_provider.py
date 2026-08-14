import json
import threading
import unittest
from threading import Event
from unittest import mock

from ai.providers import ollama


class OllamaProviderTests(unittest.TestCase):
    def _response(self, chunks):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = [
            json.dumps(chunk) for chunk in chunks
        ]
        return response

    def test_generate_accumulates_streamed_response(self):
        response = self._response([
            {"message": {"content": "hello "}, "done": False},
            {"message": {"content": "world"}, "done": True},
        ])

        with mock.patch.object(
            ollama,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            ollama,
            "is_model_loaded",
            return_value=True,
        ), mock.patch.object(
            ollama.requests,
            "post",
            return_value=response,
        ) as post:
            result, error = ollama.generate([{"role": "user", "content": "hi"}])

        self.assertEqual((result, error), ("hello world", None))
        post.assert_called_once_with(
            f"{ollama.get_base_url()}/api/chat",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
            timeout=ollama.get_timeout_seconds(),
            stream=True,
        )

    def test_generate_discards_partial_response_when_cancelled(self):
        cancel_event = Event()

        def streamed_lines(**_kwargs):
            yield json.dumps({"message": {"content": "partial "}})
            cancel_event.set()
            yield json.dumps({"message": {"content": "response"}})

        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.side_effect = streamed_lines

        with mock.patch.object(
            ollama,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            ollama,
            "is_model_loaded",
            return_value=True,
        ), mock.patch.object(
            ollama.requests,
            "post",
            return_value=response,
        ):
            result, error = ollama.generate(
                [{"role": "user", "content": "hi"}],
                cancel_event=cancel_event,
            )

        self.assertIsNone(result)
        self.assertEqual(error, "Request cancelled.")

    def test_generate_returns_stream_error(self):
        response = self._response([
            {"error": "model failed", "done": True},
        ])

        with mock.patch.object(
            ollama,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            ollama,
            "is_model_loaded",
            return_value=True,
        ), mock.patch.object(
            ollama.requests,
            "post",
            return_value=response,
        ):
            result, error = ollama.generate([{"role": "user", "content": "hi"}])

        self.assertIsNone(result)
        self.assertEqual(error, "model failed")

    def test_cancel_releases_blocked_request_creation(self):
        request_started = Event()
        release_request = Event()
        cancel_event = Event()
        results = []

        def post(*_args, **_kwargs):
            request_started.set()
            release_request.wait(timeout=1.0)
            return self._response([])

        with mock.patch.object(
            ollama,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            ollama,
            "is_model_loaded",
            return_value=True,
        ), mock.patch.object(ollama.requests, "post", side_effect=post):
            worker = threading.Thread(
                target=lambda: results.append(
                    ollama.generate([], cancel_event=cancel_event)
                )
            )
            worker.start()
            self.assertTrue(request_started.wait(timeout=1.0))
            cancel_event.set()
            worker.join(timeout=0.5)
            release_request.set()

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [(None, "Request cancelled.")])

    def test_cancel_releases_blocked_next_stream_line(self):
        line_wait_started = Event()
        release_line = Event()
        cancel_event = Event()
        results = []
        response = mock.MagicMock()
        response.__enter__.return_value = response

        def lines(**_kwargs):
            line_wait_started.set()
            release_line.wait(timeout=1.0)
            return
            yield  # pragma: no cover

        response.iter_lines.side_effect = lines

        with mock.patch.object(
            ollama,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            ollama,
            "is_model_loaded",
            return_value=True,
        ), mock.patch.object(ollama.requests, "post", return_value=response):
            worker = threading.Thread(
                target=lambda: results.append(
                    ollama.generate([], cancel_event=cancel_event)
                )
            )
            worker.start()
            self.assertTrue(line_wait_started.wait(timeout=1.0))
            cancel_event.set()
            worker.join(timeout=0.5)
            release_line.set()

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [(None, "Request cancelled.")])


if __name__ == "__main__":
    unittest.main()
