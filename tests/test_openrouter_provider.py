import threading
import unittest
from threading import Event
from types import SimpleNamespace
from unittest import mock

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Generator, Iterable, Self
    from openai.types.chat import ChatCompletionMessageParam

from ai.providers import openai as openai_provider
from ai.providers.openrouter import OpenRouterProvider


class OpenRouterProviderTests(unittest.TestCase):
    def _chunk(self, content: str|None = None, *, choices: bool = True) -> SimpleNamespace:
        if not choices:
            return SimpleNamespace(choices=[])

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=content)
                )
            ]
        )

    def _openai(self, stream: Any) -> tuple[SimpleNamespace, mock.MagicMock]:
        client = mock.MagicMock()
        client.chat.completions.create.return_value = stream

        module = SimpleNamespace(
            OpenAI=mock.MagicMock(return_value=client),
        )

        return module, client

    def test_get_api_key(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"OPENROUTER_API_KEY": "test-key"},
        ):
            self.assertEqual(
                OpenRouterProvider.get_api_key(),
                "test-key",
            )

    def test_get_model_uses_configured_value(self) -> None:
        model_name = "openai/gpt-4.1-mini"
        
        with mock.patch.dict("os.environ", {"CODA_OPENROUTER_MODEL": model_name}):
            self.assertEqual(OpenRouterProvider.get_model(), model_name)

    def test_get_model_has_no_default(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {},
            clear=True,
        ):
            self.assertEqual(
                OpenRouterProvider.get_model(),
                "",
            )

    def test_generate_returns_error_when_api_key_is_missing(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "CODA_OPENROUTER_MODEL": "openai/gpt-4.1-mini",
            },
            clear=True,
        ):
            result, error = OpenRouterProvider.generate(
                [{"role": "user", "content": "hi"}]
            )

        self.assertIsNone(result)
        self.assertEqual(
            error,
            "OPENROUTER_API_KEY is not set in env.",
        )

    def test_generate_returns_error_when_model_is_missing(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
            },
            clear=True,
        ):
            result, error = OpenRouterProvider.generate(
                [{"role": "user", "content": "hi"}]
            )

        self.assertIsNone(result)
        self.assertEqual(
            error,
            "CODA_OPENROUTER_MODEL is not set in env.",
        )

    def test_generate_accumulates_streamed_response_and_closes_stream(self) -> None:
        stream = mock.MagicMock()

        stream.__iter__.return_value = iter([
            self._chunk("hello "),
            self._chunk(choices=False),
            self._chunk(None),
            self._chunk("world"),
        ])

        openai_module, client = self._openai(stream)
        messages: Iterable[ChatCompletionMessageParam] = [{"role": "user", "content": "hi"}]

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
                "CODA_OPENROUTER_MODEL": "openai/gpt-4.1-mini",
            },
        ):
            result, error = OpenRouterProvider.generate(messages)

        self.assertEqual(
            (result, error),
            ("hello world", None),
        )

        openai_module.OpenAI.assert_called_once_with(
            api_key="test-key",
            base_url=OpenRouterProvider.get_base_url(),
        )

        client.chat.completions.create.assert_called_once_with(
            model="openai/gpt-4.1-mini",
            messages=messages,
            stream=True,
        )

        stream.close.assert_called_once_with()

    def test_generate_discards_partial_response_and_closes_when_cancelled(self) -> None:
        cancel_event = Event()

        def chunks() -> Generator[SimpleNamespace, Any, None]:
            yield self._chunk("partial ")
            cancel_event.set()
            yield self._chunk("response")

        stream = mock.MagicMock()
        stream.__iter__.side_effect = chunks

        openai_module, _client = self._openai(stream)

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
                "CODA_OPENROUTER_MODEL": "openai/gpt-4.1-mini",
            },
        ):
            result, error = OpenRouterProvider.generate(
                [{"role": "user", "content": "hi"}],
                cancel_event=cancel_event,
            )

        self.assertIsNone(result)
        self.assertEqual(
            error,
            "Request cancelled.",
        )

        stream.close.assert_called_once_with()

    def test_generate_closes_stream_when_iteration_fails(self) -> None:
        stream = mock.MagicMock()
        stream.__iter__.side_effect = RuntimeError(
            "stream failed"
        )

        openai_module, _client = self._openai(stream)

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
                "CODA_OPENROUTER_MODEL": "openai/gpt-4.1-mini",
            },
        ):
            result, error = OpenRouterProvider.generate(
                [{"role": "user", "content": "hi"}]
            )

        self.assertIsNone(result)
        self.assertEqual(
            error,
            "stream failed",
        )

        stream.close.assert_called_once_with()

    def test_cancel_releases_blocked_stream_creation(self) -> None:
        create_started = Event()
        release_create = Event()
        cancel_event = Event()

        stream = mock.MagicMock()
        openai_module, client = self._openai(stream)

        def create(**_: Any) -> mock.MagicMock:
            create_started.set()
            release_create.wait(timeout=1.0)
            return stream

        client.chat.completions.create.side_effect = create
        results = []

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
                "CODA_OPENROUTER_MODEL": "openai/gpt-4.1-mini",
            },
        ):
            worker = threading.Thread(
                target=lambda: results.append(
                    OpenRouterProvider.generate(
                        [],
                        cancel_event=cancel_event,
                    )
                )
            )

            worker.start()

            self.assertTrue(
                create_started.wait(timeout=1.0)
            )

            cancel_event.set()

            worker.join(timeout=0.5)
            release_create.set()

        self.assertFalse(worker.is_alive())

        self.assertEqual(
            results,
            [(None, "Request cancelled.")],
        )

        client.close.assert_called()

    def test_cancel_releases_blocked_next_chunk(self) -> None:
        next_chunk_started = Event()
        release_chunk = Event()
        stream_closed = Event()
        cancel_event = Event()

        class BlockedStream:
            def __iter__(self) -> Self:
                return self

            def __next__(self) -> None:
                next_chunk_started.set()
                release_chunk.wait(timeout=1.0)
                raise StopIteration

            def close(self) -> None:
                stream_closed.set()
                release_chunk.set()

        openai_module, _ = self._openai(
            BlockedStream()
        )

        results = []

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
                "CODA_OPENROUTER_MODEL": "openai/gpt-4.1-mini",
            },
        ):
            worker = threading.Thread(
                target=lambda: results.append(
                    OpenRouterProvider.generate(
                        [],
                        cancel_event=cancel_event,
                    )
                )
            )

            worker.start()

            self.assertTrue(
                next_chunk_started.wait(timeout=1.0)
            )

            cancel_event.set()

            worker.join(timeout=0.5)
            release_chunk.set()

        self.assertFalse(worker.is_alive())

        self.assertEqual(
            results,
            [(None, "Request cancelled.")],
        )

        self.assertTrue(
            stream_closed.is_set()
        )


if __name__ == "__main__":
    unittest.main()