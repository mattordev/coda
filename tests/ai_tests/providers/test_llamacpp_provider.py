import threading
import unittest
from threading import Event
from unittest import mock
import requests

from requests.exceptions import Timeout

from ai.providers import LlamacppProvider, ProviderError
from ai.providers.cancellable import CANCELLATION_MESSAGE
from tests.ai_tests.providers.base_test_llama_ai_provider import (
    BaseLlamaProviderTestCase,
)


class LlamaCppProviderTests(BaseLlamaProviderTestCase[LlamacppProvider]):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName, LlamacppProvider)

    def test_confirm_data_is_correct(self) -> None:
        expected_provider_details: GrokProvider.Details = {
            "type": LlamacppProvider.Type.LOCAL,
            "model_env": "CODA_LLAMACPP_MODEL",
            "base_url_env": "CODA_LLAMACPP_BASE_URL",
            "model_required": False,
            "default_base_url": "http://localhost:8080",
        }

        self.assertEqual(expected_provider_details, self.provider_data)

    @staticmethod
    def _stream_response() -> mock.MagicMock:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = lines
        return response

    def test_generate_streamed_response(self):
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

        self._generate_streamed_response(response, "hello world")

    def _stream_response(self, lines):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = lines
        return response

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
            self.assertRaises(
                ProviderError.InvalidModel,
                self.provider_instance.get_model,
            )

    def test_reload_config_clears_model_cache(self):
        self.provider_instance.cached_model = "cached-model"

        self.provider_instance.reload_config()

        self.assertIsNone(self.provider_instance.cached_model)

    def test_generate_discards_partial_response_when_cancelled(self):
        cancel_event = Event()

        def streamed_lines(**_kwargs):
            yield ('data: {"choices": ' '[{"delta": {"content": "partial "}}]}')

            cancel_event.set()

            yield ('data: {"choices": ' '[{"delta": {"content": "response"}}]}')

        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.side_effect = streamed_lines

        provider = self.provider_instance

        with mock.patch.object(
            provider,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            provider.active_session,
            "post",
            return_value=response,
        ):
            result = provider.generate(
                [
                    {
                        "role": "user",
                        "content": "hi",
                    }
                ],
                cancel_event=cancel_event,
            )

            self.assertEqual(result, CANCELLATION_MESSAGE)

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

        provider = self.provider_instance

        with mock.patch.object(
            provider, "get_model", return_value="test-model"
        ), mock.patch.object(provider, "active_session", return_value=session):
            worker = threading.Thread(
                target=lambda: results.append(provider.generate([], cancel_event))
            )

            worker.start()

            cancel_event.set()

            worker.join(timeout=0.5)

            release_request.set()

            provider.active_session.close.assert_called()

            self.assertEqual(
                results,
                [CANCELLATION_MESSAGE],
            )

        self.assertFalse(worker.is_alive())

    @unittest.skip("Unsure how this works")
    def test_cancel_releases_blocked_model_discovery(self):
        probe_started = Event()
        release_probe = Event()
        cancel_event = Event()
        results = []

        def get(*_args, **_kwargs):
            probe_started.set()
            release_probe.wait(timeout=1.0)
            return mock.MagicMock()

        session = mock.MagicMock()
        session.get.side_effect = get

        provider = self.provider_instance

        with mock.patch.object(
            provider,
            "active_session",
            return_value=session,
        ), mock.patch.dict(
            "os.environ",
            {
                "CODA_LLAMACPP_MODEL": "",
            },
            clear=True,
        ):
            worker = threading.Thread(
                target=lambda: results.append(provider.generate([], cancel_event))
            )

            worker.start()

            self.assertTrue(probe_started.wait(timeout=1.0))

            cancel_event.set()

            worker.join(timeout=0.5)
            release_probe.set()

        self.assertFalse(worker.is_alive())

        self.assertEqual(
            results,
            [CANCELLATION_MESSAGE],
        )

        session.close.assert_called()

    def test_generate_reports_request_timeout(self):
        session = mock.MagicMock()

        provider = self.provider_instance

        with mock.patch.object(
            provider,
            "get_model",
            return_value=("test-model", None),
        ), mock.patch.object(
            provider,
            "_generate_stream",
            side_effect=Timeout(),
        ), mock.patch.object(
            provider,
            "active_session",
            return_value=session,
        ), mock.patch.object(
            provider,
            "_get_timeout_seconds",
            return_value=5.0,
        ):
            with self.assertRaises(ProviderError.Timeout):
                provider.generate([])


if __name__ == "__main__":
    unittest.main()
