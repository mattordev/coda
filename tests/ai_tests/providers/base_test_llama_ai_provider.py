from abc import abstractmethod
from unittest import mock

from typing import TypeVar

import requests

from ai.providers.basellama import BaseLlamaProvider, LlamaType
from tests.ai_tests.providers.base_test_ai_provider import BaseAIProviderTestCase

T = TypeVar("T", bound=BaseLlamaProvider)


class BaseLlamaProviderTestCase(BaseAIProviderTestCase[T]):
    @staticmethod
    @abstractmethod
    def _create_response(lines: list[str]) -> mock.MagicMock:
        pass

    @abstractmethod
    def test_generate_streamed_response(self) -> None:
        pass

    def _generate_streamed_response(
        self, response: mock.MagicMock, response_result: str
    ) -> None:
        messages: list[LlamaType.Message] = [
            {
                "role": LlamaType.Role.USER,
                "content": "hi",
            }
        ]

        provider = self.provider_instance

        model_name = "test-model"

        provider.get_model = mock.MagicMock(return_value=model_name)
        provider._is_model_loaded = mock.MagicMock(return_value=True)
        provider.active_session.post = mock.MagicMock(return_value=response)

        result = provider.generate(messages)

        self.assertEqual(result, response_result)

        provider.active_session.post.assert_called_once_with(
            provider._get_chat_url(),
            json={"model": model_name, "messages": messages, "stream": True},
            timeout=mock.ANY,
            stream=mock.ANY,
        )

        response.close.assert_called()

    @abstractmethod
    def test_get_model_caches_first_model(self) -> None:
        pass

    def test_get_model_caches_first_model(self) -> None:
        models: list[LlamaType.Model] = [
            {"name": "first-model"},
            {"name": "second-model"},
        ]

        self.provider_instance._get_models = mock.MagicMock(return_value=models)

        with mock.patch.dict(
            "os.environ",
            {
                self.provider_data.get("model_env"): "",
                self.provider_data.get("base_url_env"): "http://localhost:8080",
            },
            clear=True,
        ):
            first_model = self.provider_instance.get_model()
            second_model = self.provider_instance.get_model()

        self.assertEqual(first_model, self.provider_instance.cached_model)
        self.assertEqual(second_model, self.provider_instance.cached_model)
