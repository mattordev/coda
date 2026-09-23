"""Tests for rolling provider telemetry snapshots and configured costs."""

import json
import unittest
from dataclasses import FrozenInstanceError, asdict

from ai.telemetry.aggregates import build_snapshot
from ai.telemetry.models import (
    AttemptOutcome,
    ProviderAttempt,
    ProviderLocality,
    UsageMetrics,
    WorkloadPurpose,
)
from ai.telemetry.pricing import (
    estimate_attempt_cost,
    load_configured_pricing,
)


class TelemetryAggregateTests(unittest.TestCase):
    @staticmethod
    def _record(outcome, *, purpose=WorkloadPurpose.CONVERSATION, metrics=None):
        return asdict(ProviderAttempt(
            trace_id="trace-1",
            timestamp=1000.0,
            purpose=purpose,
            provider="openai",
            model="model-a",
            locality=ProviderLocality.CLOUD,
            sequence=1,
            outcome=outcome,
            metrics=metrics or UsageMetrics(),
        ))

    def test_snapshot_groups_outcomes_metrics_and_workloads(self):
        attempts = [
            self._record(
                AttemptOutcome.SUCCESS,
                metrics=UsageMetrics(
                    total_duration_seconds=1.0,
                    time_to_first_token_seconds=0.2,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    throughput_tokens_per_second=5.0,
                ),
            ),
            self._record(
                AttemptOutcome.FAILURE,
                metrics=UsageMetrics(total_duration_seconds=3.0),
            ),
            self._record(
                AttemptOutcome.EMPTY_RESPONSE,
                metrics=UsageMetrics(total_duration_seconds=2.0),
            ),
            self._record(
                AttemptOutcome.CANCELLED,
                metrics=UsageMetrics(total_duration_seconds=0.5),
            ),
            self._record(AttemptOutcome.COOLDOWN_SKIPPED),
            self._record(
                AttemptOutcome.SUCCESS,
                purpose=WorkloadPurpose.INTENT_CLASSIFICATION,
            ),
        ]

        snapshot = build_snapshot(attempts, generated_at=2000.0)

        self.assertEqual(snapshot.retained_attempt_count, 6)
        self.assertEqual(len(snapshot.aggregates), 2)
        conversation = next(
            aggregate
            for aggregate in snapshot.aggregates
            if aggregate.purpose is WorkloadPurpose.CONVERSATION
        )
        self.assertEqual(conversation.event_count, 5)
        self.assertEqual(conversation.attempt_count, 4)
        self.assertEqual(conversation.success_count, 1)
        self.assertEqual(conversation.failure_count, 1)
        self.assertEqual(conversation.empty_response_count, 1)
        self.assertEqual(conversation.cancelled_count, 1)
        self.assertEqual(conversation.cooldown_skipped_count, 1)
        self.assertEqual(conversation.reliability_sample_count, 3)
        self.assertAlmostEqual(conversation.success_rate, 1 / 3)
        self.assertEqual(conversation.consecutive_failures, 2)
        self.assertEqual(conversation.last_attempt_timestamp, 1000.0)
        self.assertEqual(conversation.last_failure_timestamp, 1000.0)
        self.assertEqual(
            conversation.total_duration_seconds.sample_count,
            4,
        )
        self.assertEqual(conversation.total_duration_seconds.total, 6.5)
        self.assertEqual(
            conversation.time_to_first_token_seconds.sample_count,
            1,
        )
        self.assertEqual(conversation.input_tokens.total, 10)

    def test_unknown_measurements_and_reliability_remain_unknown(self):
        snapshot = build_snapshot(
            [self._record(AttemptOutcome.COOLDOWN_SKIPPED)],
            generated_at=2000.0,
        )

        aggregate = snapshot.aggregates[0]
        self.assertIsNone(aggregate.success_rate)
        self.assertEqual(aggregate.reliability_sample_count, 0)
        self.assertEqual(aggregate.total_duration_seconds.sample_count, 0)
        self.assertIsNone(aggregate.total_duration_seconds.total)
        self.assertIsNone(aggregate.total_duration_seconds.average)

    def test_provider_model_and_purpose_form_distinct_groups(self):
        base = self._record(AttemptOutcome.SUCCESS)
        other_model = self._record(AttemptOutcome.SUCCESS)
        other_model["model"] = "model-b"
        other_provider = self._record(AttemptOutcome.SUCCESS)
        other_provider["provider"] = "openrouter"
        other_purpose = self._record(
            AttemptOutcome.SUCCESS,
            purpose=WorkloadPurpose.INTENT_CLASSIFICATION,
        )

        snapshot = build_snapshot(
            [base, other_model, other_provider, other_purpose],
            generated_at=2000.0,
        )

        identities = {
            (aggregate.provider, aggregate.model, aggregate.purpose)
            for aggregate in snapshot.aggregates
        }
        self.assertEqual(len(identities), 4)
        self.assertIn(
            ("openai", "model-a", WorkloadPurpose.CONVERSATION),
            identities,
        )

    def test_snapshot_contract_is_read_only(self):
        snapshot = build_snapshot([], generated_at=2000.0)

        with self.assertRaises(FrozenInstanceError):
            snapshot.retained_attempt_count = 1


class ConfiguredPricingTests(unittest.TestCase):
    @staticmethod
    def _configuration(input_rate=2.0, output_rate=4.0, date="2026-09-23"):
        return json.dumps([{
            "provider": "openai",
            "model": "model-a",
            "input_per_million": input_rate,
            "output_per_million": output_rate,
            "currency": "usd",
            "effective_date": date,
            "source": "configured-test",
        }])

    @staticmethod
    def _attempt(metrics):
        return ProviderAttempt(
            trace_id="trace-1",
            timestamp=1000.0,
            purpose=WorkloadPurpose.CONVERSATION,
            provider="openai",
            model="model-a",
            locality=ProviderLocality.CLOUD,
            sequence=1,
            outcome=AttemptOutcome.SUCCESS,
            metrics=metrics,
        )

    def test_exact_configured_rate_estimates_known_token_costs(self):
        pricing = load_configured_pricing(self._configuration())
        attempt = self._attempt(UsageMetrics(
            input_tokens=1_000_000,
            output_tokens=500_000,
            total_tokens=1_500_000,
        ))

        estimate = estimate_attempt_cost(attempt, pricing)

        self.assertEqual(estimate.input_cost, 2.0)
        self.assertEqual(estimate.output_cost, 2.0)
        self.assertEqual(estimate.total_cost, 4.0)
        self.assertEqual(estimate.pricing.currency, "USD")
        self.assertEqual(estimate.pricing.effective_date, "2026-09-23")

    def test_unknown_or_invalid_pricing_does_not_invent_cost(self):
        incomplete_metrics = self._attempt(UsageMetrics(input_tokens=10))
        pricing = load_configured_pricing(self._configuration())

        self.assertIsNone(estimate_attempt_cost(incomplete_metrics, pricing))
        self.assertEqual(load_configured_pricing("not-json"), ())
        self.assertEqual(load_configured_pricing("{}"), ())


if __name__ == "__main__":
    unittest.main()
