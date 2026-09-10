from typing_extensions import Never
import unittest
from unittest import mock

from abc import abstractmethod
from typing import Generic, TypeVar

from ai.providers.provider import Provider

T = TypeVar("T", bound=Provider)


class BaseAIProviderTest(Generic[T], unittest.TestCase):
    __unittest_skip__ = True
    __unittest_skip_why__ = "BaseAIProviderTest does not contain tests itself"

    def __init__(
        self, methodName: str = "runTest", provider_type: type[T] | None = None
    ) -> None:
        if provider_type is not None:
            self.provider_instance = provider_type()

        super().__init__(methodName)

    def __init_subclass__(cls, *args: Never, **kwargs: Never) -> None:
        super().__init_subclass__(*args, **kwargs)

        cls.__unittest_skip__ = False
        cls.__unittest_skip_why__ = ""

    def _compare_configured_model(self, model_name: str) -> None:
        self.assertEqual(self.provider_instance.get_configured_model(), model_name)

    @abstractmethod
    def test_confirm_data_is_correct(self) -> None:
        raise NotImplementedError(f"{self} needs to be implemented on {self.__class__}")

    @property
    def provider_data(self) -> Provider.Details:
        return self.provider_instance.data

    def test_get_api_key(self) -> None:
        api_key_env = self.provider_data.get("api_key_env")

        with mock.patch.dict("os.environ", {api_key_env: "test-key"}):
            self.assertEqual(self.provider_instance.get_api_key(), "test-key")
