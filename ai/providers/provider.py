from __future__ import annotations

from abc import ABC, abstractmethod

from enum import StrEnum
import os
from typing import TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Required, NotRequired

class Provider(ABC):
    class Type(StrEnum):
        CLOUD = "cloud"
        LOCAL = "local"

    class Details(TypedDict):
        type: Required[Provider.Type]
        model_env: Required[str]
        model_required: Required[bool]
        base_url_env: NotRequired[str]
        api_key_env: NotRequired[str]
        default_base_url: NotRequired[str]
        default_model: NotRequired[str]

    @staticmethod
    @abstractmethod
    def _get_data() -> Details:
        pass

    def get_base_url(self) -> str|None:
        env_key = self.data.get("base_url_env", "")

        default_url = self.data.get("default_base_url", "")

        if not env_key:
            return default_url

        return os.getenv(env_key, default_url).strip()

    @property
    def data(self) -> Details:
        return self._get_data()

    def get_api_key(self) -> str:
        env_key = self.data.get("api_key_env", "")

        if not env_key:
            return ""

        return os.getenv(env_key, "").strip()

    def get_configured_model(self) -> str:
        env_key = self.data.get("model_env", "")

        return os.getenv(env_key, self.data.get("default_model", "")).strip()

    @abstractmethod
    def reload_config(self) -> None:
        pass

    @abstractmethod
    def describe(self) -> str:
        pass

    @classmethod
    def _get_timeout_seconds(cls) -> float:
        return cls._get_float_env("CODA_LLM_TIMEOUT", 45.0)

    @staticmethod
    def _get_float_env(name: str, default: float) -> float:
        value = os.getenv(name)
        if value is None:
            return default

        try:
            return float(value)
        except ValueError:
            return default
