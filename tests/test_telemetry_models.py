"""Tests for provider telemetry contracts."""

import math
import unittest
from dataclasses import asdict

from ai.telemetry.models import (
    TELEMETRY_SCHEMA_VERSION,
    AttemptOutcome,
    CapabilitySupport,
    CostEstimate,
    PricingMetadata,
    ProviderAttempt,
    ProviderCapabilities,
    ProviderErrorCategory,
    ProviderLocality,
    ProviderRequest,
    PrivacyAction,
    UsageMetrics,
    WorkloadPurpose,
)


class UsageMetricsTests(unittest.TestCase):
    """Verify measurement validation."""

    def test_unknown_measurements_are_allowed(self):
        """None represents an unavailable measurement."""
        metrics = UsageMetrics()

        self.assertIsNone(metrics.total_duration_seconds)
        self.assertIsNone(metrics.input_tokens)

    def test_valid_measurements_are_allowed(self):
        """Non-negative native measurements are preserved."""
        metrics = UsageMetrics(
            total_duration_seconds=0.75,
            input_tokens=12,
            output_tokens=8,
            total_tokens=20,
        )

        self.assertEqual(metrics.total_duration_seconds, 0.75)
        self.assertEqual(metrics.total_tokens, 20)

    def test_negative_duration_is_rejected_with_field_name(self):
        """Timing errors identify the invalid field."""
        with self.assertRaisesRegex(
            ValueError,
            "total_duration_seconds",
        ):
            UsageMetrics(total_duration_seconds=-0.1)

    def test_non_integer_token_count_is_rejected_with_field_name(self):
        """Token counts cannot be fractional."""
        with self.assertRaisesRegex(
            ValueError, "output_tokens"
        ):
            UsageMetrics(output_tokens=1.5)

    def test_boolean_token_count_is_rejected(self):
        """Booleans must not be treated as token counts."""
        with self.assertRaisesRegex(ValueError, "input_tokens"):
            UsageMetrics(input_tokens=True)

    def test_nan_duration_is_rejected(self):
        """Non-finite values would poison later aggregates."""
        with self.assertRaisesRegex(
            ValueError, "throughput_tokens_per_second"
        ):
            UsageMetrics(
                throughput_tokens_per_second=math.nan,
            )


class ProviderCapabilitiesTests(unittest.TestCase):
    """Verify provider/model capability metadata."""

    def test_unknown_is_distinct_from_unsupported(self):
        """Unknown means unverified, not known to be unavailable."""
        self.assertNotEqual(
            CapabilitySupport.UNKNOWN,
            CapabilitySupport.UNSUPPORTED,
        )

    def test_valid_local_model_capabilities_are_allowed(self):
        """Known model capabilities can be recorded."""
        capabilities = ProviderCapabilities(
            provider="ollama",
            model="qwen3:8b",
            locality=ProviderLocality.LOCAL,
            context_window_tokens=32_768,
            streaming=CapabilitySupport.SUPPORTED,
            tool_calling=CapabilitySupport.UNKNOWN,
            mcp_workflow_compatible=CapabilitySupport.UNKNOWN,
        )

        self.assertEqual(capabilities.provider, "ollama")
        self.assertEqual(capabilities.context_window_tokens, 32_768)

    def test_invalid_context_window_is_rejected(self):
        """Context window values must be positive whole token counts."""
        with self.assertRaisesRegex(ValueError, "Context window"):
            ProviderCapabilities(
                provider="openai",
                model="gpt-example",
                locality=ProviderLocality.CLOUD,
                context_window_tokens=0,
            )

    def test_capability_fields_reject_untyped_enum_values(self):
        """Capability metadata cannot accept equivalent-looking strings."""
        with self.assertRaisesRegex(ValueError, "locality"):
            ProviderCapabilities(
                provider="openai",
                model="gpt-example",
                locality="cloud",
            )

        with self.assertRaisesRegex(ValueError, "streaming"):
            ProviderCapabilities(
                provider="openai",
                model="gpt-example",
                locality=ProviderLocality.CLOUD,
                streaming="supported",
            )


class CostEstimateTests(unittest.TestCase):
    """Verify pricing provenance and cost estimate validation."""

    def test_priced_estimate_is_allowed(self):
        """A cost estimate carries its pricing provenance."""
        pricing = PricingMetadata(
            source="configured",
            currency="USD",
            effective_date="2026-09-12",
        )
        estimate = CostEstimate(
            input_cost=0.00002,
            output_cost=0.00003,
            total_cost=0.00005,
            pricing=pricing,
        )

        self.assertEqual(estimate.total_cost, 0.00005)
        self.assertEqual(estimate.pricing.currency, "USD")

    def test_empty_pricing_source_is_rejected(self):
        """Cost estimates must state where their rate came from."""
        with self.assertRaisesRegex(ValueError, "Pricing source"):
            PricingMetadata(
                source="",
                currency="USD",
                effective_date="2026-09-12",
            )

    def test_invalid_cost_identifies_the_field(self):
        """Negative or non-finite monetary values are rejected."""
        pricing = PricingMetadata(
            source="configured",
            currency="USD",
            effective_date="2026-09-12",
        )

        with self.assertRaisesRegex(ValueError, "output_cost"):
            CostEstimate(
                input_cost=None,
                output_cost=-0.01,
                total_cost=0.0,
                pricing=pricing,
            )


class ProviderAttemptTests(unittest.TestCase):
    """Verify complete, privacy-safe attempt and request contracts."""

    _TRACE_ID = "trace-123"

    def _attempt(self, **overrides):
        """Build a valid baseline attempt with optional field overrides."""
        values = {
            "trace_id": self._TRACE_ID,
            "timestamp": 1_789_732_452.91,
            "purpose": WorkloadPurpose.CONVERSATION,
            "provider": "openai",
            "model": "gpt-example",
            "locality": ProviderLocality.CLOUD,
            "sequence": 1,
            "outcome": AttemptOutcome.FAILURE,
        }
        values.update(overrides)
        return ProviderAttempt(**values)

    def test_complete_fallback_request_is_correlated(self):
        """A fallback chain records the trace, order, and linked providers."""
        first = self._attempt(
            error_category=ProviderErrorCategory.RATE_LIMIT,
            safe_failure_reason="Provider rate limit reached.",
            privacy_action=PrivacyAction.SANITIZE,
            next_provider="ollama",
            next_model="qwen3:8b",
        )
        second = self._attempt(
            provider="ollama",
            model="qwen3:8b",
            locality=ProviderLocality.LOCAL,
            sequence=2,
            outcome=AttemptOutcome.SUCCESS,
            is_fallback=True,
            privacy_action=PrivacyAction.RAW,
            previous_provider="openai",
            previous_model="gpt-example",
        )

        request = ProviderRequest(
            trace_id=self._TRACE_ID,
            timestamp=1_789_732_452.91,
            purpose=WorkloadPurpose.CONVERSATION,
            attempts=(first, second),
        )

        self.assertEqual(request.attempts[0].next_provider, "ollama")
        self.assertEqual(request.attempts[1].previous_model, "gpt-example")
        self.assertTrue(request.attempts[1].is_fallback)
        self.assertEqual(
            request.attempts[0].privacy_action,
            PrivacyAction.SANITIZE,
        )
        self.assertEqual(
            request.attempts[1].privacy_action,
            PrivacyAction.RAW,
        )

    def test_retry_flag_is_preserved(self):
        """A repeated provider attempt can be marked explicitly."""
        attempt = self._attempt(
            is_retry=True,
            previous_provider="openai",
            previous_model="gpt-example",
        )

        self.assertTrue(attempt.is_retry)
        self.assertFalse(attempt.is_fallback)

    def test_retry_and_fallback_flags_must_be_booleans(self):
        """String-like configuration values are not valid event flags."""
        with self.assertRaisesRegex(ValueError, "is_fallback"):
            self._attempt(is_fallback="yes")

    def test_attempt_rejects_untyped_enum_values(self):
        """Attempt metadata cannot accept equivalent-looking strings."""
        for field_name, value in (
            ("purpose", "conversation"),
            ("locality", "cloud"),
            ("outcome", "success"),
            ("privacy_action", "sanitize"),
            ("error_category", "timeout"),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    self._attempt(**{field_name: value})

    def test_unsupported_schema_version_is_rejected(self):
        """Records must declare the version this code understands."""
        with self.assertRaisesRegex(
            ValueError,
            "Unsupported telemetry schema version",
        ):
            self._attempt(
                schema_version=TELEMETRY_SCHEMA_VERSION + 1,
            )

    def test_request_rejects_untyped_purpose(self):
        """Request workload purposes must use the contract enum."""
        with self.assertRaisesRegex(ValueError, "purpose"):
            ProviderRequest(
                trace_id=self._TRACE_ID,
                timestamp=1_789_732_452.91,
                purpose="conversation",
            )

    def test_linked_model_requires_its_provider(self):
        """A model link cannot exist without a provider link."""
        with self.assertRaisesRegex(ValueError, "next_provider"):
            self._attempt(next_model="qwen3:8b")

    def test_request_rejects_mismatched_attempt_trace(self):
        """Every attempt in a request must have the request trace ID."""
        attempt = self._attempt(trace_id="other-trace")

        with self.assertRaisesRegex(ValueError, "trace ID"):
            ProviderRequest(
                trace_id=self._TRACE_ID,
                timestamp=1_789_732_452.91,
                purpose=WorkloadPurpose.CONVERSATION,
                attempts=(attempt,),
            )

    def test_cooldown_skip_cannot_have_a_cost(self):
        """A provider skipped before a call cannot incur request cost."""
        pricing = PricingMetadata(
            source="configured",
            currency="USD",
            effective_date="2026-09-12",
        )
        cost = CostEstimate(
            input_cost=None,
            output_cost=None,
            total_cost=0.01,
            pricing=pricing,
        )

        with self.assertRaisesRegex(ValueError, "cooldown-skipped"):
            self._attempt(
                outcome=AttemptOutcome.COOLDOWN_SKIPPED,
                cost=cost,
            )

    def test_serialized_request_has_no_content_or_credentials(self):
        """The contract has no fields for sensitive conversation data."""
        serialized = asdict(ProviderRequest(
            trace_id=self._TRACE_ID,
            timestamp=1_789_732_452.91,
            purpose=WorkloadPurpose.CONVERSATION,
            attempts=(self._attempt(),),
        ))

        self.assertNotIn("prompt", serialized)
        self.assertNotIn("response", serialized)
        self.assertNotIn("credentials", serialized)


if __name__ == "__main__":
    unittest.main()
