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
        """Get details about this provider."""
        pass

    @property
    def data(self) -> Details:
        """Property for getting the data in a public manner"""
        return self._get_data()

    @staticmethod
    @abstractmethod
    def _get_name() -> str:
        """Get the name of the provider."""
        pass

    @classmethod
    def _get_timeout_message(cls) -> str:
        """Generate a timeout message for the provider based on its name."""
        return f"{cls._get_name()} has timed out."

    @classmethod
    def get_base_url(cls) -> str | None:
        """
        Get the base url for the provider based on its base url environment variable.

        Defaults to :data:`~Provider.Details.default_base_url` if unset.
        """
        env_key = cls._get_data().get("base_url_env", "")

        default_url = cls._get_data().get("default_base_url", "")

        if not env_key:
            return default_url

        return os.getenv(env_key, default_url).strip()

    def get_api_key(self) -> str:
        """Get the API key for the provider based on its api key environment variable."""
        env_key = self.data.get("api_key_env", "")

        if not env_key:
            return ""

        return os.getenv(env_key, "").strip()

    def get_configured_model(self) -> str:
        """
        Get the configured model for the provider based on its model environment variable.

        Defaults to :data:`~Provider.Details.default_model`.
        """
        env_key = self.data.get("model_env", "")

        return os.getenv(env_key, self.data.get("default_model", "")).strip()

    @abstractmethod
    def reload_config(self) -> None:
        """Reload the provider's config"""
        pass

    @abstractmethod
    def describe(self) -> str:
        """Describe the provider"""
        pass

    @classmethod
    def _get_timeout_seconds(cls) -> float:
        """Get the number of seconds before a timeout occurs."""
        return cls._get_float_env("CODA_LLM_TIMEOUT", 45.0)

    @staticmethod
    def _get_float_env(name: str, default: float) -> float:
        """Gets an environment variable as a float.

        Returns `default` if a ValueError occurs"""
        value = os.getenv(name)
        if value is None:
            return default

        try:
            return float(value)
        except ValueError:
            return default

    @classmethod
    def _generate_request_wrapper(
        cls,
        function: Callable[Concatenate[CancellationScope, Event | None, _P], _R],
        close_callback: Callable[[], None],
        timeout_seconds: float,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _R | None:
        """
        A generic wrapper for sending a cancelable generation request
        to a provider using :func:`CancellationScope.run_cancellable`"""
        scope = CancellationScope()
        cancel_event = Event()

        close_client = scope.add(close_callback)

        try:
            return scope.run_cancellable(
                function,
                cancel_event,
                timeout_seconds,
                cls._get_timeout_message(),
                *args,
                **kwargs,
            )
        finally:
            close_client()
