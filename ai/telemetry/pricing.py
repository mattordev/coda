"""Configured token pricing and immutable per-attempt cost estimates."""

import json
import os
from dataclasses import dataclass, replace
from math import isfinite

from ai.telemetry.models import (
    AttemptOutcome,
    CostEstimate,
    PricingMetadata,
    ProviderAttempt,
)


_PRICING_ENV = "CODA_LLM_PRICING_JSON"
_TOKENS_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class ConfiguredTokenPricing:
    """Token rates configured for one exact provider/model pair."""

    provider: str
    model: str
    input_per_million: float
    output_per_million: float
    metadata: PricingMetadata


def _non_negative_number(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        return None
    return float(value)


def _parse_pricing_entry(entry):
    if not isinstance(entry, dict):
        return None

    provider = entry.get("provider")
    model = entry.get("model")
    currency = entry.get("currency")
    effective_date = entry.get("effective_date")
    source = entry.get("source", "configured")
    input_rate = _non_negative_number(entry.get("input_per_million"))
    output_rate = _non_negative_number(entry.get("output_per_million"))

    text_values = (provider, model, currency, effective_date, source)
    if not all(isinstance(value, str) and value.strip() for value in text_values):
        return None
    if input_rate is None or output_rate is None:
        return None

    return ConfiguredTokenPricing(
        provider=provider.strip().lower(),
        model=model.strip(),
        input_per_million=input_rate,
        output_per_million=output_rate,
        metadata=PricingMetadata(
            source=source.strip(),
            currency=currency.strip().upper(),
            effective_date=effective_date.strip(),
        ),
    )


def load_configured_pricing(value: str | None = None):
    """Load valid pricing entries; malformed configuration means no pricing."""
    if value is None:
        value = os.getenv(_PRICING_ENV, "")
    if not value.strip():
        return ()

    try:
        entries = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ()

    if not isinstance(entries, list):
        return ()

    return tuple(
        pricing
        for pricing in (_parse_pricing_entry(entry) for entry in entries)
        if pricing is not None
    )


def estimate_attempt_cost(attempt: ProviderAttempt, pricing=None):
    """Estimate cost only when exact pricing and both token counts are known."""
    if attempt.cost is not None:
        return attempt.cost
    if attempt.outcome is AttemptOutcome.COOLDOWN_SKIPPED:
        return None
    if attempt.model is None:
        return None

    metrics = attempt.metrics
    if metrics.input_tokens is None or metrics.output_tokens is None:
        return None

    configured_pricing = pricing
    if configured_pricing is None:
        configured_pricing = load_configured_pricing()

    rate = next(
        (
            candidate
            for candidate in configured_pricing
            if candidate.provider == attempt.provider.strip().lower()
            and candidate.model == attempt.model
        ),
        None,
    )
    if rate is None:
        return None

    input_cost = (
        metrics.input_tokens
        * rate.input_per_million
        / _TOKENS_PER_MILLION
    )
    output_cost = (
        metrics.output_tokens
        * rate.output_per_million
        / _TOKENS_PER_MILLION
    )
    if not isfinite(input_cost) or not isfinite(output_cost):
        return None
    return CostEstimate(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
        pricing=rate.metadata,
    )


def with_estimated_cost(attempt: ProviderAttempt) -> ProviderAttempt:
    """Return an attempt carrying its current configured price when possible."""
    estimate = estimate_attempt_cost(attempt)
    if estimate is None or estimate is attempt.cost:
        return attempt
    return replace(attempt, cost=estimate)
