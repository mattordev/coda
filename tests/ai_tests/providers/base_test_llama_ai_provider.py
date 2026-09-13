from __future__ import annotations

from abc import abstractmethod
import json
from unittest.mock import DEFAULT, MagicMock, patch, ANY

from threading import Event
from requests import Response
from typing import Callable, TypeVar, TYPE_CHECKING

from ai.providers.cancellable import CANCELLATION_MESSAGE

if TYPE_CHECKING:
    from typing import Any, Generator

from ai.providers.basellama import BaseLlamaProvider, LlamaType
from tests.ai_tests.providers.base_test_ai_provider import BaseAIProviderTestCase

T = TypeVar("T", bound=BaseLlamaProvider)


class BaseLlamaProviderTestCase(BaseAIProviderTestCase[T]):
    @staticmethod
    @abstractmethod
    def _create_response(lines: list[str]) -> MagicMock:
        pass

    @staticmethod
    def _convert_message(message: str | LlamaType.ChatChunk) -> str:
        line = ""

        if isinstance(message, dict):
            line = json.dumps(message)
        else:
            line = str(message)

        return line

    @classmethod
    def _convert_messages(cls, messages: list[str | LlamaType.ChatChunk]) -> list[str]:
        return [cls._convert_message(message) for message in messages]

    @classmethod
    def _stream_response(cls, messages: list[str | LlamaType.ChatChunk]) -> Response:
        lines = cls._convert_messages(messages)

        response = MagicMock(spec=Response)
        response.__enter__.return_value = response
        response.iter_lines.return_value = lines
        return response

    def _generate_response(
        self, post_response: Response, cancel_event: Event | None = None
    ) -> str | None:
        messages: list[LlamaType.Message] = [
            {
                "role": LlamaType.Role.USER,
                "content": "hi",
            }
        ]

        provider = self._create_provider()

        model_name = "test-model"

        result: str | None = None

        with (
            patch.object(provider, "get_model", return_value=model_name),
            patch.object(provider, "_is_model_loaded", return_value=True),
            patch.object(
                provider.active_session, "post", return_value=post_response
            ) as mock_post,
        ):
            result = provider.generate(messages, cancel_event)

            mock_post.assert_called_once_with(
                provider._get_chat_url(),
                json={"model": model_name, "messages": messages, "stream": True},
                timeout=ANY,
                stream=ANY,
            )

        return result

    @staticmethod
    @abstractmethod
    def _streamed_response() -> tuple[str, list[str | LlamaType.ChatChunk]]:
        raise NotImplementedError

    def test_generate_streamed_response(self) -> None:
        expected_result, messages = self._streamed_response()

        post_response = self._stream_response(messages)

        result = self._generate_response(post_response)

        self.assertEqual(result, expected_result)

    def test_get_model_caches_first_model(self) -> None:
        models: list[LlamaType.Model] = [
            {"name": "first-model"},
            {"name": "second-model"},
        ]

        provider = self._create_provider()

        with (
            patch.object(provider, "_get_models", return_value=models),
            patch.dict(
                "os.environ",
                {
                    self.provider_data.get("model_env"): "",
                    self.provider_data.get("base_url_env"): "http://localhost:8080",
                },
                clear=True,
            ),
        ):
            first_model = provider.get_model()
            second_model = provider.get_model()

        self.assertEqual(first_model, provider.cached_model)
        self.assertEqual(second_model, provider.cached_model)

    @classmethod
    @abstractmethod
    def _get_partial_response_with_cancel(
        cls,
        cancel_event: Event,
    ) -> Callable[[], Generator[str, Any, None]]:
        pass

    def test_generate_discards_partial_response_when_cancelled(self) -> None:
        cancel_event = Event()

        response = MagicMock(spec=Response)
        response.__enter__.return_value = response
        response.iter_lines.side_effect = self._get_partial_response_with_cancel(
            cancel_event
        )

        result = self._generate_response(response, cancel_event)

        self.assertEqual(result, CANCELLATION_MESSAGE)
