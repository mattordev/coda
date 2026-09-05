from abc import ABC, abstractmethod

from enum import StrEnum
import os
from typing import TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Required, NotRequired, Self

class Provider(ABC):
    class Type(StrEnum):
        CLOUD = "cloud"
        LOCAL = "local"

    class Details(TypedDict):
        type: Required[Provider.Type]
        api_key_env: NotRequired[str]
        model_env: Required[str]
        base_url_env: NotRequired[str]
        model_required: Required[bool]

    _instance = None

    def __init__(self) -> None:
        raise RuntimeError("Call instance() instead")

    @classmethod
    def instance(cls) -> Self:
        if cls._instance is None or not isinstance(cls._instance, cls):
            cls._instance = cls.__new__(cls)

        return cls._instance

    @staticmethod
    @abstractmethod
    def _get_data() -> Details:
        pass

    @staticmethod
    def get_base_url() -> str|None:
        return None

    @property
    def data(self) -> Details:
        return self._get_data()

    @classmethod
    def get_api_key(cls) -> str:
        env_key = cls.instance().data.get("api_key_env", "")

        if not env_key:
            return ""

        return os.getenv(env_key, "").strip()

    @classmethod
    def get_model(cls) -> str:
        env_key = cls.instance().data.get("model_env", "")

        return os.getenv(env_key, "").strip()

    @classmethod
    @abstractmethod
    def reload_config(cls) -> None:
        pass

    @classmethod
    @abstractmethod
    def describe(cls) -> str:
        pass

    @staticmethod
    def _get_timeout_seconds() -> float:
        try:
            return max(0.1, float(os.getenv("CODA_LLM_TIMEOUT", "45")))
        except ValueError:
            return 45.0
