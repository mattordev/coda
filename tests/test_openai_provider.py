import threading
import unittest
from threading import Event
from types import SimpleNamespace
from unittest import mock

from ai.providers import openai as openai_provider


class OpenAIProviderTests(unittest.TestCase):
    def _chunk(self, content=None, *, choices=True):
        if not choices:
            return SimpleNamespace(choices=[])

        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
        )

    def _openai(self, stream):
        client = mock.MagicMock()
        client.chat.completions.create.return_value = stream
        module = SimpleNamespace(
            OpenAI=mock.MagicMock(return_value=client),
            api_key=None,
        )
        return module, client

    def test_generate_accumulates_streamed_response_and_closes_stream(self):
        stream = mock.MagicMock()
        stream.__iter__.return_value = iter([
            self._chunk("hello "),
            self._chunk(choices=False),
            self._chunk(None),
            self._chunk("world"),
        ])
        openai_module, client = self._openai(stream)
        messages = [{"role": "user", "content": "hi"}]

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "test-key", "CODA_OPENAI_MODEL": "test-model"},
        ):
            result, error = openai_provider.generate(messages)

        self.assertEqual((result, error), ("hello world", None))
        client.chat.completions.create.assert_called_once_with(
            model="test-model",
            messages=messages,
            stream=True,
        )
        stream.close.assert_called_once_with()

    def test_generate_discards_partial_response_and_closes_when_cancelled(self):
        cancel_event = Event()

        def chunks():
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
            {"OPENAI_API_KEY": "test-key"},
        ):
            result, error = openai_provider.generate(
                [{"role": "user", "content": "hi"}],
                cancel_event=cancel_event,
            )

        self.assertIsNone(result)
        self.assertEqual(error, "Request cancelled.")
        stream.close.assert_called_once_with()

    def test_generate_closes_stream_when_iteration_fails(self):
        stream = mock.MagicMock()
        stream.__iter__.side_effect = RuntimeError("stream failed")
        openai_module, _client = self._openai(stream)

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "test-key"},
        ):
            result, error = openai_provider.generate(
                [{"role": "user", "content": "hi"}]
            )

        self.assertIsNone(result)
        self.assertEqual(error, "stream failed")
        stream.close.assert_called_once_with()

    def test_cancel_releases_blocked_stream_creation(self):
        create_started = Event()
        release_create = Event()
        cancel_event = Event()
        stream = mock.MagicMock()
        openai_module, client = self._openai(stream)

        def create(**_kwargs):
            create_started.set()
            release_create.wait(timeout=1.0)
            return stream

        client.chat.completions.create.side_effect = create
        results = []

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            worker = threading.Thread(
                target=lambda: results.append(
                    openai_provider.generate([], cancel_event=cancel_event)
                )
            )
            worker.start()
            self.assertTrue(create_started.wait(timeout=1.0))
            cancel_event.set()
            worker.join(timeout=0.5)
            release_create.set()

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [(None, "Request cancelled.")])
        client.close.assert_called()

    def test_cancel_releases_blocked_next_chunk(self):
        next_chunk_started = Event()
        release_chunk = Event()
        stream_closed = Event()
        cancel_event = Event()

        class BlockedStream:
            def __iter__(self):
                return self

            def __next__(self):
                next_chunk_started.set()
                release_chunk.wait(timeout=1.0)
                raise StopIteration

            def close(self):
                stream_closed.set()
                release_chunk.set()

        openai_module, _client = self._openai(BlockedStream())
        results = []

        with mock.patch.object(
            openai_provider,
            "openai",
            openai_module,
        ), mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            worker = threading.Thread(
                target=lambda: results.append(
                    openai_provider.generate([], cancel_event=cancel_event)
                )
            )
            worker.start()
            self.assertTrue(next_chunk_started.wait(timeout=1.0))
            cancel_event.set()
            worker.join(timeout=0.5)
            release_chunk.set()

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [(None, "Request cancelled.")])
        self.assertTrue(stream_closed.is_set())


if __name__ == "__main__":
    unittest.main()
