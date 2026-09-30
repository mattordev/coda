"""Pure rolling aggregation over retained privacy-safe provider attempts."""

import time
from collections import defaultdict
from math import isfinite

from ai.telemetry.models import (
    AttemptOutcome,
    CostAggregate,
    MetricAggregate,
    PricingMetadata,
    ProviderTelemetryAggregate,
    TelemetrySnapshot,
    WorkloadPurpose,
)


_METRIC_FIELDS = (
    "total_duration_seconds",
    "time_to_first_token_seconds",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "throughput_tokens_per_second",
)


def _known_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not isfinite(value) or value < 0:
        return None
    return value


def _metric(values):
    if not values:
        return MetricAggregate(sample_count=0, total=None, average=None)
    total = sum(values)
    if not isfinite(total):
        return MetricAggregate(sample_count=0, total=None, average=None)
    return MetricAggregate(
        sample_count=len(values),
        total=total,
        average=total / len(values),
    )


def _new_group(provider, model, purpose):
    return {
        "provider": provider,
        "model": model,
        "purpose": purpose,
        "event_count": 0,
        "attempt_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "empty_response_count": 0,
        "cancelled_count": 0,
        "cooldown_skipped_count": 0,
        "consecutive_failures": 0,
        "last_attempt_timestamp": None,
        "last_failure_timestamp": None,
        "metrics": {field: [] for field in _METRIC_FIELDS},
        "costs": defaultdict(lambda: {
            "count": 0,
            "input": [],
            "output": [],
            "total": 0.0,
        }),
    }


def _identity(record):
    if not isinstance(record, dict):
        return None

    provider = record.get("provider")
    model = record.get("model")
    try:
        purpose = WorkloadPurpose(record.get("purpose"))
        outcome = AttemptOutcome(record.get("outcome"))
    except (TypeError, ValueError):
        return None

    if not isinstance(provider, str) or not provider.strip():
        return None
    if model is not None and (
        not isinstance(model, str) or not model.strip()
    ):
        return None
    return provider, model, purpose, outcome


def _add_metrics(group, record):
    metrics = record.get("metrics")
    if not isinstance(metrics, dict):
        return
    for field_name in _METRIC_FIELDS:
        value = _known_number(metrics.get(field_name))
        if value is not None:
            group["metrics"][field_name].append(value)


def _add_cost(group, record):
    cost = record.get("cost")
    if not isinstance(cost, dict):
        return
    pricing = cost.get("pricing")
    if not isinstance(pricing, dict):
        return

    source = pricing.get("source")
    currency = pricing.get("currency")
    effective_date = pricing.get("effective_date")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (source, currency, effective_date)
    ):
        return

    total_cost = _known_number(cost.get("total_cost"))
    if total_cost is None:
        return

    key = (source, currency, effective_date)
    accumulator = group["costs"][key]
    accumulator["count"] += 1
    accumulator["total"] += total_cost
    for field_name, target in (
        ("input_cost", "input"),
        ("output_cost", "output"),
    ):
        value = _known_number(cost.get(field_name))
        if value is not None:
            accumulator[target].append(value)


def _cost_aggregates(group):
    totals = []
    for (source, currency, effective_date), values in sorted(
        group["costs"].items()
    ):
        input_cost = sum(values["input"]) if values["input"] else None
        output_cost = sum(values["output"]) if values["output"] else None
        if not isfinite(values["total"]):
            continue
        if input_cost is not None and not isfinite(input_cost):
            continue
        if output_cost is not None and not isfinite(output_cost):
            continue
        totals.append(CostAggregate(
            pricing=PricingMetadata(
                source=source,
                currency=currency,
                effective_date=effective_date,
            ),
            priced_attempt_count=values["count"],
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=values["total"],
        ))
    return tuple(totals)


def build_snapshot(attempts, generated_at=None):
    """Build an immutable snapshot from the current bounded attempt window."""
    records = tuple(attempts)
    groups = {}

    for record in records:
        identity = _identity(record)
        if identity is None:
            continue
        provider, model, purpose, outcome = identity
        key = (provider, model, purpose)
        group = groups.setdefault(key, _new_group(provider, model, purpose))
        group["event_count"] += 1
        timestamp = _known_number(record.get("timestamp"))
        if outcome is not AttemptOutcome.COOLDOWN_SKIPPED:
            group["attempt_count"] += 1
            if timestamp is not None:
                group["last_attempt_timestamp"] = max(
                    timestamp,
                    group["last_attempt_timestamp"] or 0,
                )

        outcome_field = {
            AttemptOutcome.SUCCESS: "success_count",
            AttemptOutcome.FAILURE: "failure_count",
            AttemptOutcome.EMPTY_RESPONSE: "empty_response_count",
            AttemptOutcome.CANCELLED: "cancelled_count",
            AttemptOutcome.COOLDOWN_SKIPPED: "cooldown_skipped_count",
        }[outcome]
        group[outcome_field] += 1

        if outcome is AttemptOutcome.SUCCESS:
            group["consecutive_failures"] = 0
        elif outcome in (
            AttemptOutcome.FAILURE,
            AttemptOutcome.EMPTY_RESPONSE,
        ):
            group["consecutive_failures"] += 1
            if timestamp is not None:
                group["last_failure_timestamp"] = max(
                    timestamp,
                    group["last_failure_timestamp"] or 0,
                )

        if outcome is not AttemptOutcome.COOLDOWN_SKIPPED:
            _add_metrics(group, record)
            _add_cost(group, record)

    aggregates = []
    for key in sorted(
        groups,
        key=lambda item: (item[0], item[1] or "", item[2].value),
    ):
        group = groups[key]
        reliability_samples = (
            group["success_count"]
            + group["failure_count"]
            + group["empty_response_count"]
        )
        success_rate = (
            group["success_count"] / reliability_samples
            if reliability_samples
            else None
        )
        metrics = group["metrics"]
        aggregates.append(ProviderTelemetryAggregate(
            provider=group["provider"],
            model=group["model"],
            purpose=group["purpose"],
            event_count=group["event_count"],
            attempt_count=group["attempt_count"],
            success_count=group["success_count"],
            failure_count=group["failure_count"],
            empty_response_count=group["empty_response_count"],
            cancelled_count=group["cancelled_count"],
            cooldown_skipped_count=group["cooldown_skipped_count"],
            reliability_sample_count=reliability_samples,
            success_rate=success_rate,
            consecutive_failures=group["consecutive_failures"],
            last_attempt_timestamp=group["last_attempt_timestamp"],
            last_failure_timestamp=group["last_failure_timestamp"],
            total_duration_seconds=_metric(metrics["total_duration_seconds"]),
            time_to_first_token_seconds=_metric(
                metrics["time_to_first_token_seconds"]
            ),
            input_tokens=_metric(metrics["input_tokens"]),
            output_tokens=_metric(metrics["output_tokens"]),
            total_tokens=_metric(metrics["total_tokens"]),
            throughput_tokens_per_second=_metric(
                metrics["throughput_tokens_per_second"]
            ),
            costs=_cost_aggregates(group),
        ))

    return TelemetrySnapshot(
        generated_at=time.time() if generated_at is None else generated_at,
        retained_attempt_count=len(records),
        aggregates=tuple(aggregates),
    )
