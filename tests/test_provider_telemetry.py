"""Tests for native provider timing and usage metadata."""

import json
import unittest
from threading import Event
from types import SimpleNamespace
from unittest import mock

from ai.providers import gemini, grok, llamacpp, ollama, openai, openrouter
from ai.providers.telemetry import ProviderMetricsCollector


def _clock(*values):
    ticks = iter(values)
    return lambda: next(ticks)


def _openai_chunk(content=None, *, model=None, usage=None, choices=True):
    return SimpleNamespace(
        model=model,
        usage=usage,
        choices=(
            [SimpleNamespace(delta=SimpleNamespace(content=content))]
            if choices
            else []
        ),
    )


class ProviderMetricsCollectorTests(unittest.TestCase):
    def test_openai_usage_and_measured_timing_are_normalized(self):
        collector = ProviderMetricsCollector(
            "requested-model",
            clock=_clock(10.0, 11.5, 14.0),
        )
        collector.observe_openai_chunk(
            _openai_chunk(model="resolved-model")
        )
        collector.observe_content("hello")
        collector.observe_openai_chunk(_openai_chunk(
            choices=False,
            usage=SimpleNamespace(
                prompt_tokens=3,
                completion_tokens=4,
                total_tokens=7,
            ),
        ))

        result = collector.finish(response="hello")

        self.assertEqual(result.model, "resolved-model")
        self.assertEqual(result.metrics.total_duration_seconds, 4.0)
        self.assertEqual(result.metrics.time_to_first_token_seconds, 1.5)
        self.assertEqual(result.metrics.input_tokens, 3)
        self.assertEqual(result.metrics.output_tokens, 4)
        self.assertEqual(result.metrics.total_tokens, 7)
        self.assertEqual(result.metrics.throughput_tokens_per_second, 1.0)

    def test_ollama_native_nanoseconds_are_normalized_to_seconds(self):
        collector = ProviderMetricsCollector(clock=_clock(10.0, 11.0, 20.0))
        collector.observe_content("hello")
        collector.observe_ollama_chunk({
            "model": "resolved-model",
            "prompt_eval_count": 3,
            "eval_count": 4,
            "total_duration": 2_000_000_000,
            "eval_duration": 500_000_000,
        })

        result = collector.finish(response="hello")

        self.assertEqual(result.model, "resolved-model")
        self.assertEqual(result.metrics.total_duration_seconds, 2.0)
        self.assertEqual(result.metrics.time_to_first_token_seconds, 1.0)
        self.assertEqual(result.metrics.total_tokens, 7)
        self.assertEqual(result.metrics.throughput_tokens_per_second, 8.0)

    def test_absent_or_invalid_usage_remains_unknown(self):
        collector = ProviderMetricsCollector(clock=_clock(1.0, 2.0))
        collector.observe_openai_chunk({
            "usage": {
                "prompt_tokens": True,
                "completion_tokens": -1,
                "total_tokens": "unknown",
            }
        })

        result = collector.finish(error="provider failed")

        self.assertEqual(result.error, "provider failed")
        self.assertIsNone(result.metrics.time_to_first_token_seconds)
        self.assertIsNone(result.metrics.input_tokens)
        self.assertIsNone(result.metrics.output_tokens)
        self.assertIsNone(result.metrics.total_tokens)
        self.assertIsNone(result.metrics.throughput_tokens_per_second)


class OpenAICompatibleTelemetryTests(unittest.TestCase):
    ADAPTERS = (
        (openai, {"OPENAI_API_KEY": "key", "CODA_OPENAI_MODEL": "requested"}),
        (
            openrouter,
            {
                "OPENROUTER_API_KEY": "key",
                "CODA_OPENROUTER_MODEL": "requested",
            },
        ),
        (grok, {"XAI_API_KEY": "key", "CODA_GROK_MODEL": "requested"}),
        (gemini, {"GEMINI_API_KEY": "key", "CODA_GEMINI_MODEL": "requested"}),
    )

    @staticmethod
    def _module(stream):
        client = mock.MagicMock()
        client.chat.completions.create.return_value = stream
        return SimpleNamespace(
            OpenAI=mock.MagicMock(return_value=client),
            api_key=None,
        )

    def test_real_shaped_streams_capture_model_usage_and_content(self):
        usage = SimpleNamespace(
            prompt_tokens=5,
            completion_tokens=2,
            total_tokens=7,
        )

        for adapter, environment in self.ADAPTERS:
            with self.subTest(adapter=adapter.__name__):
                stream = mock.MagicMock()
                stream.__iter__.return_value = iter([
                    _openai_chunk("hello", model="resolved-model"),
                    _openai_chunk(choices=False, usage=usage),
                ])

                with mock.patch.object(
                    adapter,
                    "openai",
                    self._module(stream),
                ), mock.patch.dict("os.environ", environment, clear=True):
                    result = adapter.generate_with_metadata([])

                self.assertEqual(result.response, "hello")
                self.assertIsNone(result.error)
                self.assertEqual(result.model, "resolved-model")
                self.assertEqual(result.metrics.input_tokens, 5)
                self.assertEqual(result.metrics.output_tokens, 2)
                self.assertEqual(result.metrics.total_tokens, 7)
                self.assertIsNotNone(
                    result.metrics.time_to_first_token_seconds
                )

    def test_absent_usage_stays_unknown(self):
        stream = mock.MagicMock()
        stream.__iter__.return_value = iter([_openai_chunk("hello")])

        with mock.patch.object(
            openai,
            "openai",
            self._module(stream),
        ), mock.patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "key"},
            clear=True,
        ):
            result = openai.generate_with_metadata([])

        self.assertIsNone(result.metrics.input_tokens)
        self.assertIsNone(result.metrics.output_tokens)
        self.assertIsNone(result.metrics.total_tokens)

    def test_errors_and_cancellation_still_include_safe_timing(self):
        failed_stream = mock.MagicMock()
        failed_stream.__iter__.side_effect = RuntimeError("stream failed")

        with mock.patch.object(
            openai,
            "openai",
            self._module(failed_stream),
        ), mock.patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "key"},
            clear=True,
        ):
            failed = openai.generate_with_metadata([])

        cancelled = Event()
        cancelled.set()
        unused_stream = mock.MagicMock()

        with mock.patch.object(
            openai,
            "openai",
            self._module(unused_stream),
        ), mock.patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "key"},
            clear=True,
        ):
            stopped = openai.generate_with_metadata([], cancelled)

        self.assertEqual(failed.error, "stream failed")
        self.assertIsNotNone(failed.metrics.total_duration_seconds)
        self.assertIsNone(failed.metrics.time_to_first_token_seconds)
        self.assertEqual(stopped.error, "Request cancelled.")
        self.assertIsNotNone(stopped.metrics.total_duration_seconds)
        self.assertIsNone(stopped.metrics.time_to_first_token_seconds)


class NativeAdapterTelemetryTests(unittest.TestCase):
    def test_ollama_final_chunk_supplies_native_metrics(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = [json.dumps({
            "model": "resolved-ollama",
            "message": {"content": "hello"},
            "done": True,
            "total_duration": 2_000_000_000,
            "prompt_eval_count": 3,
            "eval_count": 4,
            "eval_duration": 500_000_000,
        })]
        session = mock.MagicMock()
        session.post.return_value = response

        with mock.patch.object(
            ollama,
            "get_model",
            return_value=("requested-ollama", None),
        ), mock.patch.object(
            ollama,
            "is_model_loaded",
            return_value=True,
        ), mock.patch.object(
            ollama.requests,
            "Session",
            return_value=session,
        ):
            result = ollama.generate_with_metadata([])

        self.assertEqual(result.response, "hello")
        self.assertEqual(result.model, "resolved-ollama")
        self.assertEqual(result.metrics.total_duration_seconds, 2.0)
        self.assertEqual(result.metrics.input_tokens, 3)
        self.assertEqual(result.metrics.output_tokens, 4)
        self.assertEqual(result.metrics.total_tokens, 7)
        self.assertEqual(result.metrics.throughput_tokens_per_second, 8.0)

    def test_llamacpp_usage_chunk_supplies_native_counts(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = [
            'data: {"model":"resolved-llama","choices":'
            '[{"delta":{"content":"hello"}}]}',
            'data: {"model":"resolved-llama","choices":[],"usage":'
            '{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}',
            "data: [DONE]",
        ]
        session = mock.MagicMock()
        session.post.return_value = response

        with mock.patch.object(
            llamacpp,
            "get_model",
            return_value=("requested-llama", None),
        ), mock.patch.object(
            llamacpp.requests,
            "Session",
            return_value=session,
        ):
            result = llamacpp.generate_with_metadata([])

        self.assertEqual(result.response, "hello")
        self.assertEqual(result.model, "resolved-llama")
        self.assertEqual(result.metrics.input_tokens, 3)
        self.assertEqual(result.metrics.output_tokens, 4)
        self.assertEqual(result.metrics.total_tokens, 7)


if __name__ == "__main__":
    unittest.main()
