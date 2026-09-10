import unittest
from unittest import mock

from abc import abstractmethod
from typing import Generic, TypeVar

from ai.providers.provider import Provider

T = TypeVar("T", bound=Provider)


class BaseAIProviderTestCase(Generic[T], unittest.TestCase):
    __unittest_skip__ = True
    __unittest_skip_why__ = "does not contain tests itself"

    def __init__(
        self, methodName: str = "runTest", provider_type: type[T] | None = None
    ) -> None:
        if provider_type is not None:
            self.provider_instance = provider_type()
            self._enable_unit_testing()
        else:
            self._disable_unit_testing()

        super().__init__(methodName)

    @classmethod
    def _enable_unit_testing(cls) -> None:
        cls.__unittest_skip__ = False
        cls.__unittest_skip_why__ = ""

    @classmethod
    def _disable_unit_testing(cls) -> None:
        cls.__unittest_skip__ = True
        cls.__unittest_skip_why__ = f"{cls.__name__} does not contain tests itself"

    def _compare_configured_model(self, model_name: str) -> None:
        self.assertEqual(self.provider_instance.get_configured_model(), model_name)

    @abstractmethod
    def test_confirm_data_is_correct(self) -> None:
        raise NotImplementedError(f"{self} needs to be implemented on {self.__class__}")

    @property
    def provider_data(self) -> Provider.Details:
        return self.provider_instance.data

    def test_get_api_key(self) -> None:
        api_key_env = self.provider_data.get("api_key_env", None)

        if api_key_env is None:
            provider_class_name = self.provider_instance.__class__.__name__

            self.skipTest(f"{provider_class_name} does not require an api key")

        with mock.patch.dict("os.environ", {api_key_env: "test-key"}):
            self.assertEqual(self.provider_instance.get_api_key(), "test-key")

    def test_get_configured_model_uses_configured_value(self) -> None:
        model_name = "configured-model"
        model_env = self.provider_data.get("model_env")

        with mock.patch.dict("os.environ", {model_env: model_name}):
            self._compare_configured_model(model_name)
