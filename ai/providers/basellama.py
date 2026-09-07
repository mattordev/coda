import requests

from abc import abstractmethod
from enum import StrEnum
from typing import TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import NotRequired

from . import Provider


class LlamaRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LlamaModel(TypedDict):
    name: NotRequired[str]
    model: NotRequired[str]


class BaseLlamaProvider(Provider):

    def __init__(self) -> None:
        self.active_session = requests.Session()
        super().__init__()

    @classmethod
    def get_base_url(cls) -> str | None:
        base_url = super().get_base_url()

        if not base_url:
            return None

        return base_url.rstrip("/")

    @staticmethod
    def preferred_models() -> list[str]:
        return []

    @classmethod
    @abstractmethod
    def get_models_url(cls) -> str:
        pass

    def reload_config(self) -> None:
        self._model_cache = None

    def describe(self) -> str:
        model, error = self.get_model()
        if error:
            return f"{self._get_name()} (model resolution failed: {error})"

        return f"{self._get_name()} (model: {model})"

    @abstractmethod
    def _get_models(
        self, url: str, *, raise_exception: bool = True
    ) -> list[LlamaModel]:
        pass

    def get_model(self) -> tuple[str | None, str | None]:
        configured_model = self.get_configured_model()
        if configured_model:
            return configured_model, None

        if self._model_cache:
            return self._model_cache, None

        model_env = self.data.get("model_env")
        models: list[LlamaModel] = []
        try:
            models = self._get_models(self.get_models_url())
        except Exception as exc:
            return (
                None,
                f"Could not fetch {self._get_name()} models automatically. Set {model_env} explicitly. Details: {exc}",
            )

        if not models:
            return (
                None,
                f"No {self._get_name()} models were found at the configured host. Set {model_env} after pulling a model on the host.",
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
            return (
                None,
                f"{self._get_name()} returned models but none included a usable name.",
            )

        _model_cache = selected_model
        return _model_cache, None
