import threading
from typing import TYPE_CHECKING, Callable
import unittest
from threading import Event
from unittest import mock

from requests.exceptions import Timeout

if TYPE_CHECKING:
    from typing import Any, Generator

from ai.providers import LlamacppProvider, ProviderError
from ai.providers.basellama import LlamaType
from ai.providers.cancellable import CANCELLATION_MESSAGE
from tests.ai_tests.providers.base_test_llama_ai_provider import (
    BaseLlamaProviderTestCase,
)


class LlamaCppProviderTests(BaseLlamaProviderTestCase[LlamacppProvider]):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName, LlamacppProvider)

    def test_confirm_data_is_correct(self) -> None:
        expected_provider_details: LlamacppProvider.Details = {
            "type": LlamacppProvider.Type.LOCAL,
            "model_env": "CODA_LLAMACPP_MODEL",
            "base_url_env": "CODA_LLAMACPP_BASE_URL",
            "model_required": False,
            "default_base_url": "http://localhost:8080",
        }

        self.assertEqual(expected_provider_details, self.provider_data)

    @classmethod
    def _get_partial_response_with_cancel(
        cls,
        cancel_event: Event,
    ) -> Callable[[], Generator[str, Any, None]]:
        def partial_response(**kwargs: Any) -> Generator[str, Any, None]:
            yield 'data: {"choices": ' '[{"delta": {"content": "partial "}}]}'
            cancel_event.set()
            yield 'data: {"choices": ' '[{"delta": {"content": "response"}}]}'

        return partial_response

    @staticmethod
    def _streamed_response() -> tuple[str, list[str | LlamaType.ChatChunk]]:
        return "hello world", [
            'data: {"choices": [{"delta": {"content": "hello "}}]}',
            "",
            'data: {"choices": []}',
            'data: {"choices": [{"delta": {"content": null}}]}',
            'data: {"choices": [{"delta": {"content": "world"}}]}',
            "data: [DONE]",
        ]

    def test_get_model_reports_when_server_has_no_models(self):
        provider = self._create_provider()

        with mock.patch.dict(
            "os.environ",
            {
                "CODA_LLAMACPP_MODEL": "",
            },
            clear=True,
        ):
            self.assertRaises(
                ProviderError.InvalidModel,
                provider.get_model,
            )

    def test_reload_config_clears_model_cache(self):
        provider = self._create_provider()

        provider.cached_model = "cached-model"

        provider.reload_config()

        self.assertIsNone(provider.cached_model)

    def test_cancel_releases_blocked_request_creation(self):
        request_started = Event()
        release_request = Event()
        cancel_event = Event()
        results: list[str | None] = []

        def post(*_args, **_kwargs):
            request_started.set()
            release_request.wait(timeout=1.0)
            return self._stream_response([])

        session = mock.MagicMock()
        session.post.side_effect = post

        provider = self._create_provider()

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

        provider = self._create_provider()

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
