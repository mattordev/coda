import unittest
from unittest.mock import patch

from abc import abstractmethod
from typing import Generic, ParamSpec, TypeVar

from ai.providers.provider import Provider

T = TypeVar("T", bound=Provider)
V = TypeVar("V")
P = ParamSpec("P")


class BaseAIProviderTestCase(Generic[T], unittest.TestCase):
    __unittest_skip__ = True
    __unittest_skip_why__ = "does not contain tests itself"
    _provider_type: type[T]

    def __init__(
        self, methodName: str = "runTest", provider_type: type[T] | None = None
    ) -> None:
        if provider_type is not None:
            self.__class__._provider_type = provider_type
            self._enable_unit_testing()
        else:
            self._disable_unit_testing()

        super().__init__(methodName)

    @classmethod
    def _create_provider(cls) -> T:
        """Generates a new provider each call."""
        return cls._provider_type()

    @classmethod
    def _enable_unit_testing(cls) -> None:
        """Enables unit testing"""
        cls.__unittest_skip__ = False
        cls.__unittest_skip_why__ = ""

    @classmethod
    def _disable_unit_testing(cls) -> None:
        """Disables unit testing for this file"""
        cls.__unittest_skip__ = True
        cls.__unittest_skip_why__ = f"{cls.__name__} does not contain tests itself"

    def _compare_configured_model(self, model_name: str) -> None:
        """Compare the configured model against the model we expect"""
        self.assertEqual(self._create_provider().get_configured_model(), model_name)

    @abstractmethod
    def test_confirm_data_is_correct(self) -> None:
        """Test to confirm that the data on the Provider matches the data in the test"""
        raise NotImplementedError(f"{self} needs to be implemented on {self.__class__}")

    @property
    def provider_data(self) -> Provider.Details:
        """The data of the provider"""
        return self._create_provider().data

    def test_get_api_key(self) -> None:
        """Test getting the API key from the provider data

        NOTE:
        Test will be skipped if the api key is unset, as this means the provider doesn't require an API key
        """
        api_key_env = self.provider_data.get("api_key_env", None)

        if api_key_env is None:
            provider_class_name = self._create_provider().__class__.__name__

            self.skipTest(f"{provider_class_name} does not require an api key")

        with patch.dict("os.environ", {api_key_env: "test-key"}):
            self.assertEqual(self._create_provider().get_api_key(), "test-key")

    def test_get_configured_model_uses_configured_value(self) -> None:
        """Tests that when we set the configured model, it is loaded correctly"""
        model_name = "configured-model"
        model_env = self.provider_data.get("model_env")

        with patch.dict("os.environ", {model_env: model_name}):
            self._compare_configured_model(model_name)
