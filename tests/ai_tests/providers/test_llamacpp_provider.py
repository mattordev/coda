import json
import threading
import unittest
from threading import Event
from unittest import mock

from requests.exceptions import Timeout

from ai.providers import llamacpp


class LlamaCppProviderTests(unittest.TestCase):
    def setUp(self):
        llamacpp.reload_config()

    def tearDown(self):
        llamacpp.reload_config()

    def _stream_response(self, lines):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = lines
        return response

    def test_get_model_uses_configured_value(self):
        with mock.patch.dict(
            "os.environ",
            {
                "CODA_LLAMACPP_MODEL": "configured-model",
            },
            clear=True,
        ):
            model, error = llamacpp.get_model()

        self.assertEqual(model, "configured-model")
        self.assertIsNone(error)

    def test_get_model_discovers_first_server_model_and_caches_it(self):
        response = mock.MagicMock()
        response.json.return_value = {
            "data": [
                {"id": "first-model"},
                {"id": "second-model"},
            ]
        }

        http_client = mock.MagicMock()
        http_client.get.return_value = response

        with mock.patch.dict(
            "os.environ",
            {
                "CODA_LLAMACPP_MODEL": "",
                "CODA_LLAMACPP_BASE_URL": "http://localhost:8080",
            },
            clear=True,
        ):
            first_model, first_error = llamacpp.get_model(
                http_client
            )
            second_model, second_error = llamacpp.get_model(
                http_client
            )

        self.assertEqual(first_model, "first-model")
        self.assertIsNone(first_error)

        self.assertEqual(second_model, "first-model")
        self.assertIsNone(second_error)

        http_client.get.assert_called_once_with(
            "http://localhost:8080/v1/models",
            timeout=min(llamacpp.get_timeout_seconds(), 10),
        )

    def test_get_model_reports_when_server_has_no_models(self):
        response = mock.MagicMock()
        response.json.return_value = {
            "data": [],
        }

        http_client = mock.MagicMock()
        http_client.get.return_value = response

        with mock.patch.dict(
            "os.environ",
            {
                "CODA_LLAMACPP_MODEL": "",
            },
            clear=True,
        ):
            model, error = llamacpp.get_model(http_client)

        self.assertIsNone(model)
        self.assertEqual(
            error,
            "No model was reported by the configured llama.cpp server.",
        )

    def test_reload_config_clears_model_cache(self):
        llamacpp._model_cache = "cached-model"

        llamacpp.reload_config()

        self.assertIsNone(llamacpp._model_cache)

    def test_generate_accumulates_streamed_response(self):
        response = self._stream_response(
            [
                'data: {"choices": [{"delta": {"content": "hello "}}]}',
                "",
                'data: {"choices": []}',
                'data: {"choices": [{"delta": {"content": null}}]}',
                'data: {"choices": [{"delta": {"content": "world"}}]}',
                "data: [DONE]",
            ]
        )

        session = mock.MagicMock()
        session.post.return_value = response

        messages = [
            {
                "role": "user",
                "content": "hi",
            }
        ]

        with mock.patch.object(
            llamacpp,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            llamacpp.requests,
            "Session",
            return_value=session,
        ):
            result, error = llamacpp.generate(messages)

        self.assertEqual(
            (result, error),
            ("hello world", None),
        )

        session.post.assert_called_once_with(
            f"{llamacpp.get_base_url()}/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": messages,
                "stream": True,
            },
            timeout=llamacpp.get_timeout_seconds(),
            stream=True,
        )

        response.close.assert_called()

    def test_generate_discards_partial_response_when_cancelled(self):
        cancel_event = Event()

        def streamed_lines(**_kwargs):
            yield (
                'data: {"choices": '
                '[{"delta": {"content": "partial "}}]}'
            )

            cancel_event.set()

            yield (
                'data: {"choices": '
                '[{"delta": {"content": "response"}}]}'
            )

        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.side_effect = streamed_lines

        session = mock.MagicMock()
        session.post.return_value = response

        with mock.patch.object(
            llamacpp,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            llamacpp.requests,
            "Session",
            return_value=session,
        ):
            result, error = llamacpp.generate(
                [
                    {
                        "role": "user",
                        "content": "hi",
                    }
                ],
                cancel_event=cancel_event,
            )

        self.assertIsNone(result)
        self.assertEqual(
            error,
            "Request cancelled.",
        )

        response.close.assert_called()

    def test_cancel_releases_blocked_request_creation(self):
        request_started = Event()
        release_request = Event()
        cancel_event = Event()
        results = []

        def post(*_args, **_kwargs):
            request_started.set()
            release_request.wait(timeout=1.0)
            return self._stream_response([])

        session = mock.MagicMock()
        session.post.side_effect = post

        with mock.patch.object(
            llamacpp,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            llamacpp.requests,
            "Session",
            return_value=session,
        ):
            worker = threading.Thread(
                target=lambda: results.append(
                    llamacpp.generate(
                        [],
                        cancel_event=cancel_event,
                    )
                )
            )

            worker.start()

            self.assertTrue(
                request_started.wait(timeout=1.0)
            )

            cancel_event.set()

            worker.join(timeout=0.5)
            release_request.set()

        self.assertFalse(worker.is_alive())

        self.assertEqual(
            results,
            [(None, "Request cancelled.")],
        )

        session.close.assert_called()

    def test_cancel_releases_blocked_model_discovery(self):
        probe_started = Event()
        release_probe = Event()
        cancel_event = Event()
        results = []

        session = mock.MagicMock()

        def get(*_args, **_kwargs):
            probe_started.set()
            release_probe.wait(timeout=1.0)
            return mock.MagicMock()

        session.get.side_effect = get

        with mock.patch.object(
            llamacpp.requests,
            "Session",
            return_value=session,
        ), mock.patch.dict(
            "os.environ",
            {
                "CODA_LLAMACPP_MODEL": "",
            },
            clear=True,
        ):
            worker = threading.Thread(
                target=lambda: results.append(
                    llamacpp.generate(
                        [],
                        cancel_event=cancel_event,
                    )
                )
            )

            worker.start()

            self.assertTrue(
                probe_started.wait(timeout=1.0)
            )

            cancel_event.set()

            worker.join(timeout=0.5)
            release_probe.set()

        self.assertFalse(worker.is_alive())

        self.assertEqual(
            results,
            [(None, "Request cancelled.")],
        )

        session.close.assert_called()

    def test_generate_reports_request_timeout(self):
        session = mock.MagicMock()

        with mock.patch.object(
            llamacpp,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            llamacpp,
            "_generate_stream",
            side_effect=Timeout(),
        ), mock.patch.object(
            llamacpp.requests,
            "Session",
            return_value=session,
        ), mock.patch.object(
            llamacpp,
            "get_timeout_seconds",
            return_value=5.0,
        ):
            result, error = llamacpp.generate([])

        self.assertIsNone(result)
        self.assertEqual(
            error,
            "llama.cpp timed out after 5.0 seconds.",
        )


if __name__ == "__main__":
    unittest.main()