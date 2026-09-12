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
