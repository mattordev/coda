"""Shared provider-call telemetry collection without routing side effects."""

import time
from dataclasses import dataclass
from math import isfinite

from ai.telemetry.models import UsageMetrics


@dataclass(frozen=True)
class ProviderCallResult:
    """A provider result plus normalized metadata for one completed call."""

    response: str | None
    error: str | None
    model: str | None
    metrics: UsageMetrics

    # iterates through and returns self.response and self.error for use later
    def __iter__(self):
        """Preserve response/error unpacking for existing callers."""
        yield self.response
        yield self.error


def _field(value, name):
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _non_negative_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _seconds(value, divisor):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        return None
    return value / divisor


class ProviderMetricsCollector:
    """Collect timing and usage while keeping unsupported values explicit."""

    def __init__(self, requested_model=None, clock=None):
        self._clock = clock or time.monotonic
        self._started_at = self._clock()
        self._first_content_at = None
        self._first_generated_at = None
        self._native_total_duration = None
        self._native_output_duration = None
        self.model = requested_model or None
        self.input_tokens = None
        self.output_tokens = None
        self.total_tokens = None
        self.prompt_duration_seconds = None
        self.load_duration_seconds = None
        self.reasoning_tokens = None

    def observe_content(self, content):
        if content and self._first_content_at is None:
            self._first_content_at = self._clock()
            if self._first_generated_at is None:
                self._first_generated_at = self._first_content_at

    def _observe_reasoning(self, content):
        # Observe arrival only: never retain or serialize hidden reasoning text.
        if isinstance(content, str) and content and self._first_generated_at is None:
            self._first_generated_at = self._clock()

    def observe_openai_chunk(self, chunk):
        """Read optional model and usage fields from OpenAI-shaped chunks."""
        model = _field(chunk, "model")
        if isinstance(model, str) and model.strip():
            self.model = model.strip()

        choices = _field(chunk, 'choices')
        if choices:
            delta = _field(choices[0], 'delta')
            self._observe_reasoning(_field(delta, 'reasoning_content') or _field(delta, 'reasoning'))
        timings = _field(chunk, 'timings')
        prompt_duration = _seconds(_field(timings, 'prompt_ms'), 1000)
        decode_duration = _seconds(_field(timings, 'predicted_ms'), 1000)
        if prompt_duration is not None:
            self.prompt_duration_seconds = prompt_duration
        if decode_duration is not None:
            self._native_output_duration = decode_duration

        usage = _field(chunk, "usage")
        if usage is None:
            return

        for target, source in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'),
                               ('total_tokens', 'total_tokens')):
            value = _non_negative_int(_field(usage, source))
            if value is not None:
                setattr(self, target, value)
        reasoning = _non_negative_int(_field(_field(usage, 'completion_tokens_details'), 'reasoning_tokens'))
        if reasoning is not None:
            self.reasoning_tokens = reasoning

    def observe_ollama_chunk(self, chunk):
        """Read model, counts and nanosecond timings from an Ollama chunk."""
        model = _field(chunk, "model")
        if isinstance(model, str) and model.strip():
            self.model = model.strip()

        self._observe_reasoning(_field(_field(chunk, 'message'), 'thinking'))
        prompt_duration = _seconds(_field(chunk, 'prompt_eval_duration'), 1_000_000_000)
        load_duration = _seconds(_field(chunk, 'load_duration'), 1_000_000_000)
        if prompt_duration is not None:
            self.prompt_duration_seconds = prompt_duration
        if load_duration is not None:
            self.load_duration_seconds = load_duration

        input_tokens = _non_negative_int(_field(chunk, "prompt_eval_count"))
        output_tokens = _non_negative_int(_field(chunk, "eval_count"))

        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens

        if self.input_tokens is not None and self.output_tokens is not None:
            self.total_tokens = self.input_tokens + self.output_tokens

        total_duration = _seconds(_field(chunk, "total_duration"), 1_000_000_000)
        output_duration = _seconds(_field(chunk, "eval_duration"), 1_000_000_000)

        if total_duration is not None:
            self._native_total_duration = total_duration
        if output_duration is not None:
            self._native_output_duration = output_duration

    def finish(self, response=None, error=None):
        """Create an immutable result using native timing when supplied."""
        finished_at = self._clock()
        measured_duration = max(0.0, finished_at - self._started_at)
        total_duration = self._native_total_duration if self._native_total_duration is not None else measured_duration

        ttft = None
        if self._first_content_at is not None:
            ttft = max(0.0, self._first_content_at - self._started_at)
        first_generated = None
        if self._first_generated_at is not None:
            first_generated = max(0.0, self._first_generated_at - self._started_at)

        throughput = None
        if self.output_tokens is not None:
            throughput_duration = self._native_output_duration if self._native_output_duration is not None else total_duration
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
                time_to_first_generated_token_seconds=first_generated,
                time_to_first_visible_token_seconds=ttft,
                prompt_duration_seconds=self.prompt_duration_seconds,
                decode_duration_seconds=self._native_output_duration,
                load_duration_seconds=self.load_duration_seconds,
                reasoning_tokens=self.reasoning_tokens,
            ),
        )
