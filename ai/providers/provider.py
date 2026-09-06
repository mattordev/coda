from __future__ import annotations

from abc import ABC, abstractmethod

from enum import StrEnum
import os
from typing import ParamSpec, TypeVar, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable, Concatenate, Required, NotRequired
    from threading import Event

from ai.providers.cancellable import CancellationScope

_P = ParamSpec("_P")
_R = TypeVar("_R")


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

    @staticmethod
    @abstractmethod
    def _get_name() -> str:
        pass

    @classmethod
    def _get_timeout_message(cls) -> str:
        return f"{cls._get_name()} has timed out."

    def get_base_url(self) -> str | None:
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

    @classmethod
    def _run_cancellable_wrapper(
        cls,
        function: Callable[Concatenate[CancellationScope, Event | None, _P], _R],
        timeout_seconds: float,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _R | None:
        scope = CancellationScope()
        cancel_event = Event()

        return scope.run_cancellable(
            function,
            cancel_event,
            timeout_seconds,
            cls._get_timeout_message(),
            *args,
            **kwargs,
        )
