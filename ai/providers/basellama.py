import requests

from abc import abstractmethod, ABC
from enum import StrEnum
from typing import TypedDict, TYPE_CHECKING
from threading import Event

if TYPE_CHECKING:
    from typing import NotRequired, Required

from ai.providers.cancellable import CancellationScope
from . import Provider


class LlamaType:
    class Role(StrEnum):
        SYSTEM = "system"
        USER = "user"
        ASSISTANT = "assistant"
        TOOL = "tool"

    class Message(TypedDict):
        role: Required[LlamaType.Role]
        content: Required[str]

    class Model(TypedDict):
        name: NotRequired[str]
        model: NotRequired[str]

    class ChatChunk(TypedDict):
        model: str
        message: NotRequired[LlamaType.Message]
        error: NotRequired[str]
        response: NotRequired[str]
        done: bool


class BaseLlamaProvider(Provider, ABC):

    class _ChatAPIJson(TypedDict):
        model: Required[str]
        messages: Required[list[LlamaType.Message]]
        stream: NotRequired[bool]

    def __init__(self) -> None:
        self.active_session = requests.Session()
        super().__init__()

    @staticmethod
    def preferred_models() -> list[str]:
        return []

    @classmethod
    def _get_cold_start_timeout_seconds(cls) -> float:
        return cls._get_timeout_seconds()

    @classmethod
    def _calculate_generate_timeout_seconds(cls) -> float:
        timeout_seconds = cls._get_timeout_seconds()

        max_seconds = max(timeout_seconds, cls._get_cold_start_timeout_seconds())

        max_seconds += min(timeout_seconds, 10) * 2

        return max_seconds

    @classmethod
    def get_base_url(cls) -> str | None:
        base_url = super().get_base_url()

        if not base_url:
            return None

        return base_url.rstrip("/")

    @classmethod
    @abstractmethod
    def _get_chat_url(cls) -> str:
        pass

    @classmethod
    @abstractmethod
    def _get_models_url(cls) -> str:
        pass

    @abstractmethod
    def _process_response(
        self, response: requests.Response, cancel_event: Event | None = None
    ) -> list[str]:
        pass

    def reload_config(self) -> None:
        self._model_cache: str | None = None

    def _is_model_loaded(self, model_name: str) -> bool:
        return True

    def _generate_stream(
        self,
        scope: CancellationScope,
        cancel_event: Event | None,
        model_name: str,
        messages: list[LlamaType.Message],
        timeout_seconds: float,
    ) -> str:
        chat_url = self._get_chat_url()

        post_json: BaseLlamaProvider._ChatAPIJson = {
            "model": model_name,
            "messages": messages,
            "stream": True,
        }

        with self.active_session.post(
            chat_url, json=post_json, timeout=timeout_seconds, stream=True
        ) as response:
            close_response = scope.add(response.close)
            response.raise_for_status()
            response_parts: list[str] = []

            try:
                response_parts = self._process_response(response, cancel_event)
            except self.ProviderError:
                raise

            close_response()
            return "".join(response_parts)

    def _generate_request(
        self,
        scope: CancellationScope,
        cancel_event: Event | None,
        messages: list[LlamaType.Message],
        *,
        query_model_loaded: bool = False,
    ) -> str:
        try:
            model_name = self.get_model()
        except self.ProviderError:
            raise

        timeout_seconds = self._get_timeout_seconds()
        timeout_message = f"{self._get_name()} timed out after {timeout_seconds} seconds while using model: {model_name}."

        if query_model_loaded:
            model_loaded = self._is_model_loaded(model_name)

            if not model_loaded:
                timeout_seconds = self._get_cold_start_timeout_seconds()
                timeout_message = (
                    f"{self._get_name()} timed out after {timeout_seconds} seconds while starting model {model_name}. "
                    f"The model may still be loading; try again in a moment or "
                    f"increase the CODA_OLLAMA_COLD_START_TIMEOUT environment variable."
                )

        try:
            result = self._generate_stream(
                scope,
                cancel_event,
                model_name,
                messages,
                timeout_seconds,
            )
        except InterruptedError as e:
            raise self.ProviderError(f"Request cancelled. Details: {e}")
        except requests.Timeout as e:
            raise self.ProviderError(f"{timeout_message}. Details: {e}")
        except self.ProviderError:
            raise
        except Exception as e:
            raise self.ProviderError(f"Uncaught exception. Details: {e}")

        return (result or "").strip()

    def describe(self) -> str:
        model: str | None = None
        try:
            model = self.get_model()
        except self.ProviderError as e:
            return f"{self._get_name()} (model resolution failed: {e})"

        return f"{self._get_name()} (model: {model})"

    @abstractmethod
    def _get_models(
        self, url: str, *, raise_exception: bool = True
    ) -> list[LlamaType.Model]:
        pass

    def get_model(self) -> str:
        configured_model = self.get_configured_model()
        if configured_model:
            return configured_model

        if self._model_cache:
            return self._model_cache

        model_env = self.data.get("model_env")
        models: list[LlamaType.Model] = []
        try:
            models = self._get_models(self._get_models_url())
        except Exception as exc:
            raise self.ProviderError(
                f"Could not fetch {self._get_name()} models automatically. Set {model_env} explicitly. Details: {exc}"
            )

        if not models:
            raise self.ProviderError(
                f"No {self._get_name()} models were found at the configured host. Set {model_env} after pulling a model on the host."
            )

        model_names: list[str] = []
        for model in models:
            model_name = model.get("name") or model.get("model")
            if model_name:
                model_names.append(model_name)

        selected_model: str | None = None
        for preferred_model in self.preferred_models():
            if preferred_model in model_names:
                selected_model = preferred_model
                break

        if selected_model is None:
            selected_model = model_names[0] if model_names else None

        if not selected_model:
            raise self.ProviderError(
                f"{self._get_name()} returned models but none included a usable name."
            )

        self._model_cache = selected_model
        return self._model_cache

    def generate(
        self, messages: list[LlamaType.Message]
    ) -> tuple[str | None, str | None] | None:
        session = requests.Session()

        timeout_seconds = max(
            self._get_timeout_seconds(), self._get_cold_start_timeout_seconds()
        )
        timeout_seconds += min(self._get_timeout_seconds(), 10) * 2

        try:
            return (
                self._generate_request_wrapper(
                    self._generate_request,
                    session.close,
                    timeout_seconds,
                    messages,
                ),
                None,
            )
        except InterruptedError:
            return None, "Request cancelled."
        except Exception as exc:
            return None, str(exc)
