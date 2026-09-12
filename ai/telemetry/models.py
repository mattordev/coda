"""Typed contracts for provider telemetry."""

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite


TELEMETRY_SCHEMA_VERSION = 1


class WorkloadPurpose(str, Enum):
    """The kind of work that caused the provider request."""

    CONVERSATION = "conversation"
    INTENT_CLASSIFICATION = "intent_classification"


class ProviderLocality(str, Enum):
    """Where the provider processes the request."""

    LOCAL = "local"
    CLOUD = "cloud"
    UNKNOWN = "unknown"


class AttemptOutcome(str, Enum):
    """The normalized result of a provider attempt."""

    SUCCESS = "success"
    FAILURE = "failure"
    EMPTY_RESPONSE = "empty_response"
    CANCELLED = "cancelled"
    COOLDOWN_SKIPPED = "cooldown_skipped"


@dataclass(frozen=True)
class UsageMetrics:
    """Native timing and token measurements for one provider attempt."""

    total_duration_seconds: float | None = None
    time_to_first_token_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    throughput_tokens_per_second: float | None = None

    def __post_init__(self) -> None:
        """Validate supplied measurements without inventing unknown values."""
        for field_name in (
            "total_duration_seconds",
            "time_to_first_token_seconds",
            "throughput_tokens_per_second",
        ):
            value = getattr(self, field_name)

            if value is None:
                continue

            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(
                    f"{field_name} must be a finite non-negative number or None."
                )

        for field_name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
        ):
            value = getattr(self, field_name)

            if value is None:
                continue

            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    f"{field_name} must be a non-negative integer or None."
                )


class CapabilitySupport(str, Enum):
    """A capability state where unknown is distinct from unsupported."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Known capabilities for a provider and, when known, its model."""

    provider: str
    model: str | None
    locality: ProviderLocality
    context_window_tokens: int | None = None
    streaming: CapabilitySupport = CapabilitySupport.UNKNOWN
    tool_calling: CapabilitySupport = CapabilitySupport.UNKNOWN
    mcp_workflow_compatible: CapabilitySupport = CapabilitySupport.UNKNOWN

    def __post_init__(self) -> None:
        """Validate capability identity and numeric limits."""
        if not self.provider.strip():
            raise ValueError("Provider cannot be empty.")

        if self.model is not None and not self.model.strip():
            raise ValueError("Model cannot be blank when supplied.")

        if self.context_window_tokens is not None and (
            isinstance(self.context_window_tokens, bool)
            or not isinstance(self.context_window_tokens, int)
            or self.context_window_tokens <= 0
        ):
            raise ValueError(
                "Context window must be a positive integer or None."
            )


@dataclass(frozen=True)
class PricingMetadata:
    """The provenance of pricing used for a cost estimate."""

    source: str
    currency: str
    effective_date: str

    def __post_init__(self) -> None:
        """Require enough provenance to explain an estimate."""
        if not self.source.strip():
            raise ValueError("Pricing source cannot be empty.")

        if not self.currency.strip():
            raise ValueError("Pricing currency cannot be empty.")

        if not self.effective_date.strip():
            raise ValueError("Pricing effective date cannot be empty.")


@dataclass(frozen=True)
class CostEstimate:
    """A non-billing-grade cost estimate for one provider attempt."""

    input_cost: float | None
    output_cost: float | None
    total_cost: float
    pricing: PricingMetadata

    def __post_init__(self) -> None:
        """Reject impossible monetary values."""
        for field_name in (
            "input_cost",
            "output_cost",
            "total_cost",
        ):
            value = getattr(self, field_name)

            if value is None:
                continue

            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(
                    f"{field_name} must be a finite non-negative number or None."
                )


@dataclass(frozen=True)
class ProviderAttempt:
    """One privacy-safe provider attempt."""

    trace_id: str
    timestamp: float
    purpose: WorkloadPurpose
    provider: str
    model: str | None
    locality: ProviderLocality
    sequence: int
    outcome: AttemptOutcome
    metrics: UsageMetrics = field(default_factory=UsageMetrics)
    schema_version: int = TELEMETRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate the stable attempt contract."""
        if self.schema_version != TELEMETRY_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported telemetry schema version: {self.schema_version}."
            )

        if not self.trace_id.strip():
            raise ValueError("Trace ID cannot be empty.")

        if not self.provider.strip():
            raise ValueError("Provider cannot be empty.")

        if self.model is not None and not self.model.strip():
            raise ValueError("Model cannot be blank when supplied.")

        if self.timestamp < 0:
            raise ValueError("Timestamp cannot be negative.")

        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("Attempt sequence must be a positive integer.")


@dataclass(frozen=True)
class ProviderRequest:
    """A request trace containing ordered provider attempts."""

    trace_id: str
    timestamp: float
    purpose: WorkloadPurpose
    attempts: tuple[ProviderAttempt, ...] = ()
    schema_version: int = TELEMETRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Keep request and attempt identity and ordering consistent."""
        if self.schema_version != TELEMETRY_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported telemetry schema version: {self.schema_version}."
            )

        if not self.trace_id.strip():
            raise ValueError("Trace ID cannot be empty.")

        if self.timestamp < 0:
            raise ValueError("Timestamp cannot be negative.")

        expected_sequences = list(range(1, len(self.attempts) + 1))
        actual_sequences = [attempt.sequence for attempt in self.attempts]

        if actual_sequences != expected_sequences:
            raise ValueError("Attempt sequences must be contiguous and ordered.")

        for attempt in self.attempts:
            if attempt.trace_id != self.trace_id:
                raise ValueError("Attempt trace ID must match its request.")

            if attempt.purpose is not self.purpose:
                raise ValueError("Attempt purpose must match its request.")
