"""Typed contracts for provider telemetry."""

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite


TELEMETRY_SCHEMA_VERSION = 1

def _require_enum(
    value: object,
    enum_type: type[Enum],
    field_name: str,
) -> None:
    """Require a value to be an instance of the expected enum type."""
    if not isinstance(value, enum_type):
        raise ValueError(
            f"{field_name} must be a {enum_type.__name__}."
        )


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


class ProviderErrorCategory(str, Enum):
    """A content-safe normalized category for provider failures."""

    AUTHENTICATION = "authentication"
    CONFIGURATION = "configuration"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN = "unknown"


class PrivacyAction(str, Enum):
    """The privacy-policy action applied to an attempt."""

    RAW = "raw"
    SANITIZE = "sanitize"
    SUMMARIZE = "summarize"
    BLOCK = "block"


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
        _require_enum(
            self.locality,
            ProviderLocality,
            "locality",
        )

        for field_name in (
            "streaming",
            "tool_calling",
            "mcp_workflow_compatible",
        ):
            _require_enum(
                getattr(self, field_name),
                CapabilitySupport,
                field_name,
            )

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
    privacy_action: PrivacyAction | None = None
    is_retry: bool = False
    is_fallback: bool = False
    metrics: UsageMetrics = field(default_factory=UsageMetrics)
    cost: CostEstimate | None = None
    safe_failure_reason: str | None = None # human readable ver of error_catagory
    previous_provider: str | None = None
    previous_model: str | None = None
    next_provider: str | None = None
    next_model: str | None = None
    error_category: ProviderErrorCategory | None = None
    schema_version: int = TELEMETRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate the stable attempt contract."""
        _require_enum(
            self.purpose,
            WorkloadPurpose,
            "purpose",
        )
        _require_enum(
            self.locality,
            ProviderLocality,
            "locality",
        )
        _require_enum(
            self.outcome,
            AttemptOutcome,
            "outcome",
        )

        if self.privacy_action is not None:
            _require_enum(
                self.privacy_action,
                PrivacyAction,
                "privacy_action",
            )

        if self.error_category is not None:
            _require_enum(
                self.error_category,
                ProviderErrorCategory,
                "error_category",
            )

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

        for field_name in ("is_retry", "is_fallback"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a boolean.")

        if (
            self.safe_failure_reason is not None
            and not self.safe_failure_reason.strip()
        ):
            raise ValueError(
                "Safe failure reason cannot be blank when supplied."
            )

        for provider_field, model_field in (
            ("previous_provider", "previous_model"),
            ("next_provider", "next_model"),
        ):
            linked_provider = getattr(self, provider_field)
            linked_model = getattr(self, model_field)

            if linked_provider is None and linked_model is not None:
                raise ValueError(
                    f"{model_field} requires {provider_field}."
                )

            if (
                linked_provider is not None
                and not linked_provider.strip()
            ):
                raise ValueError(
                    f"{provider_field} cannot be blank when supplied."
                )

            if linked_model is not None and not linked_model.strip():
                raise ValueError(
                    f"{model_field} cannot be blank when supplied."
                )

            if (
                self.outcome is AttemptOutcome.COOLDOWN_SKIPPED
                and self.cost is not None
            ):
                raise ValueError(
                    "A cooldown-skipped provider cannot have a cost estimate."
                )

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
        _require_enum(
            self.purpose,
            WorkloadPurpose,
            "purpose",
        )

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


@dataclass(frozen=True)
class MetricAggregate:
    """A sample count, total and average for one optional measurement."""

    sample_count: int
    total: float | int | None
    average: float | None

    def __post_init__(self) -> None:
        """Keep unknown metrics explicit and populated metrics consistent."""
        if (
            isinstance(self.sample_count, bool)
            or not isinstance(self.sample_count, int)
            or self.sample_count < 0
        ):
            raise ValueError("Metric sample count must be non-negative.")

        if self.sample_count == 0:
            if self.total is not None or self.average is not None:
                raise ValueError(
                    "Metrics without samples must have unknown totals and averages."
                )
            return

        for field_name in ("total", "average"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(
                    f"Metric {field_name} must be a finite non-negative number."
                )


@dataclass(frozen=True)
class CostAggregate:
    """Historical costs sharing one currency and pricing provenance."""

    pricing: PricingMetadata
    priced_attempt_count: int
    input_cost: float | None
    output_cost: float | None
    total_cost: float

    def __post_init__(self) -> None:
        """Reject incomplete counts and invalid accumulated costs."""
        if not isinstance(self.pricing, PricingMetadata):
            raise ValueError("Cost aggregate pricing must be PricingMetadata.")
        if (
            isinstance(self.priced_attempt_count, bool)
            or not isinstance(self.priced_attempt_count, int)
            or self.priced_attempt_count < 1
        ):
            raise ValueError("Priced attempt count must be positive.")

        for field_name in ("input_cost", "output_cost", "total_cost"):
            value = getattr(self, field_name)
            if value is None and field_name != "total_cost":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(
                    f"Cost aggregate {field_name} must be non-negative."
                )


@dataclass(frozen=True)
class ProviderTelemetryAggregate:
    """Rolling measurements for one provider/model/workload combination."""

    provider: str
    model: str | None
    purpose: WorkloadPurpose
    event_count: int
    attempt_count: int
    success_count: int
    failure_count: int
    empty_response_count: int
    cancelled_count: int
    cooldown_skipped_count: int
    reliability_sample_count: int
    success_rate: float | None
    consecutive_failures: int
    last_attempt_timestamp: float | None
    last_failure_timestamp: float | None
    total_duration_seconds: MetricAggregate
    time_to_first_token_seconds: MetricAggregate
    input_tokens: MetricAggregate
    output_tokens: MetricAggregate
    total_tokens: MetricAggregate
    throughput_tokens_per_second: MetricAggregate
    costs: tuple[CostAggregate, ...] = ()

    def __post_init__(self) -> None:
        """Validate aggregate identity, counters and reliability rate."""
        _require_enum(self.purpose, WorkloadPurpose, "purpose")
        if not self.provider.strip():
            raise ValueError("Provider cannot be empty.")
        if self.model is not None and not self.model.strip():
            raise ValueError("Model cannot be blank when supplied.")

        count_fields = (
            "event_count",
            "attempt_count",
            "success_count",
            "failure_count",
            "empty_response_count",
            "cancelled_count",
            "cooldown_skipped_count",
            "reliability_sample_count",
            "consecutive_failures",
        )
        for field_name in count_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be non-negative.")

        outcome_count = (
            self.success_count
            + self.failure_count
            + self.empty_response_count
            + self.cancelled_count
            + self.cooldown_skipped_count
        )
        if self.event_count != outcome_count:
            raise ValueError("Event count must equal the outcome counts.")
        if self.attempt_count != self.event_count - self.cooldown_skipped_count:
            raise ValueError("Attempt count must exclude cooldown skips.")
        expected_reliability_samples = (
            self.success_count
            + self.failure_count
            + self.empty_response_count
        )
        if self.reliability_sample_count != expected_reliability_samples:
            raise ValueError(
                "Reliability samples must exclude cancellation and cooldown skips."
            )
        if self.consecutive_failures > (
            self.failure_count + self.empty_response_count
        ):
            raise ValueError("Consecutive failures exceed recorded failures.")

        for field_name in (
            "last_attempt_timestamp",
            "last_failure_timestamp",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be non-negative or None.")

        if self.success_rate is not None and (
            isinstance(self.success_rate, bool)
            or not isinstance(self.success_rate, (int, float))
            or not isfinite(self.success_rate)
            or not 0 <= self.success_rate <= 1
        ):
            raise ValueError("Success rate must be between zero and one.")
        if (self.reliability_sample_count == 0) != (self.success_rate is None):
            raise ValueError("Success rate must match its reliability samples.")


@dataclass(frozen=True)
class TelemetrySnapshot:
    """An immutable read-only view over the retained telemetry window."""

    generated_at: float
    retained_attempt_count: int
    aggregates: tuple[ProviderTelemetryAggregate, ...] = ()

    def __post_init__(self) -> None:
        """Validate snapshot time and retained record count."""
        if (
            isinstance(self.generated_at, bool)
            or not isinstance(self.generated_at, (int, float))
            or not isfinite(self.generated_at)
            or self.generated_at < 0
        ):
            raise ValueError("Snapshot timestamp must be non-negative.")
        if (
            isinstance(self.retained_attempt_count, bool)
            or not isinstance(self.retained_attempt_count, int)
            or self.retained_attempt_count < 0
        ):
            raise ValueError("Retained attempt count must be non-negative.")
