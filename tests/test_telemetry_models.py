"""Tests for provider telemetry contracts."""

import math
import unittest

from ai.telemetry.models import (
    CapabilitySupport,
    CostEstimate,
    PricingMetadata,
    ProviderCapabilities,
    ProviderLocality,
    UsageMetrics,
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


if __name__ == "__main__":
    unittest.main()
