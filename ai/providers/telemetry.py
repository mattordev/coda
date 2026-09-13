"""Shared provider-call telemetry collection without routing side effects."""

import time
from dataclasses import dataclass

from ai.telemetry.models import UsageMetrics


@dataclass(frozen=True)
class ProviderCallResult:
    """A provider result plus normalized metadata for one completed call."""

    response: str | None
    error: str | None
    model: str | None
    metrics: UsageMetrics


def _field(value, name):
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _non_negative_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _positive_seconds_from_nanoseconds(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
    ):
        return None
    return value / 1_000_000_000


class ProviderMetricsCollector:
    """Collect timing and usage while keeping unsupported values explicit."""

    def __init__(self, requested_model=None, clock=None):
        self._clock = clock or time.monotonic
        self._started_at = self._clock()
        self._first_content_at = None
        self._native_total_duration = None
        self._native_output_duration = None
        self.model = requested_model or None
        self.input_tokens = None
        self.output_tokens = None
        self.total_tokens = None

    def observe_content(self, content):
        if content and self._first_content_at is None:
            self._first_content_at = self._clock()

    def observe_openai_chunk(self, chunk):
        """Read optional model and usage fields from OpenAI-shaped chunks."""
        model = _field(chunk, "model")
        if isinstance(model, str) and model.strip():
            self.model = model.strip()

        usage = _field(chunk, "usage")
        if usage is None:
            return

        self.input_tokens = _non_negative_int(
            _field(usage, "prompt_tokens")
        )
        self.output_tokens = _non_negative_int(
            _field(usage, "completion_tokens")
        )
        self.total_tokens = _non_negative_int(
            _field(usage, "total_tokens")
        )

    def observe_ollama_chunk(self, chunk):
        """Read model, counts and nanosecond timings from an Ollama chunk."""
        model = _field(chunk, "model")
        if isinstance(model, str) and model.strip():
            self.model = model.strip()

        input_tokens = _non_negative_int(_field(chunk, "prompt_eval_count"))
        output_tokens = _non_negative_int(_field(chunk, "eval_count"))

        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens

        if self.input_tokens is not None and self.output_tokens is not None:
            self.total_tokens = self.input_tokens + self.output_tokens

        total_duration = _positive_seconds_from_nanoseconds(
            _field(chunk, "total_duration")
        )
        output_duration = _positive_seconds_from_nanoseconds(
            _field(chunk, "eval_duration")
        )

        if total_duration is not None:
            self._native_total_duration = total_duration
        if output_duration is not None:
            self._native_output_duration = output_duration

    def finish(self, response=None, error=None):
        """Create an immutable result using native timing when supplied."""
        finished_at = self._clock()
        measured_duration = max(0.0, finished_at - self._started_at)
        total_duration = self._native_total_duration or measured_duration

        ttft = None
        if self._first_content_at is not None:
            ttft = max(0.0, self._first_content_at - self._started_at)

        throughput = None
        if self.output_tokens is not None:
            throughput_duration = self._native_output_duration or total_duration
            if throughput_duration > 0:
                throughput = self.output_tokens / throughput_duration

        return ProviderCallResult(
            response=response,
            error=error,
            model=self.model,
            metrics=UsageMetrics(
                total_duration_seconds=total_duration,
                time_to_first_token_seconds=ttft,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                total_tokens=self.total_tokens,
                throughput_tokens_per_second=throughput,
            ),
        )
