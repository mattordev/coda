import os
import time
from dataclasses import replace
from threading import Event
from uuid import uuid4
from ai.request_context import trace_id as request_trace_id, current_request

from ai.providers import registry
from ai.providers.telemetry import ProviderCallResult
from ai.providers.completion import COMPLETION_ERRORS, PARTIAL_STREAM_ERROR
from ai.providers.workloads import conversation_options
from ai.privacy.detector import analyze_privacy
from ai.privacy import policy
from ai.telemetry import logger
from ai.telemetry.models import (
    AttemptOutcome,
    PrivacyAction,
    ProviderAttempt,
    ProviderLocality,
    UsageMetrics,
    WorkloadPurpose,
)
import utils.llm_service as llm_service
import utils.runtime_state as runtime_state


def _debug_print(*args, **kwargs):
    runtime_state.debug_print(*args, **kwargs)


def _is_cancelled(cancel_event: Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _provider_locality(provider: str) -> ProviderLocality:
    provider_type = registry.get_provider_type(provider)
    if provider_type == "local":
        return ProviderLocality.LOCAL
    if provider_type == "cloud":
        return ProviderLocality.CLOUD
    return ProviderLocality.UNKNOWN


def _privacy_action(provider: str, risk: float, privacy_result) -> PrivacyAction:
    if registry.get_provider_type(provider) != "cloud":
        return PrivacyAction.RAW

    if privacy_result is None:
        privacy_result = {
            "risk": risk,
            "level": policy.get_risk_level(risk),
            "categories": [],
            "matches": [],
        }

    return PrivacyAction(policy.get_cloud_action(privacy_result))


def _record_conversation_attempt(
    *,
    provider: str,
    risk: float,
    privacy_result,
    trace_id: str | None,
    sequence: int,
    outcome: AttemptOutcome,
    result=None,
    is_retry: bool = False,
    is_fallback: bool = False,
    purpose: WorkloadPurpose = WorkloadPurpose.CONVERSATION,
) -> None:
    structured_result = (
        result
        if isinstance(result, ProviderCallResult)
        else None
    )
    logger.record_attempt(ProviderAttempt(
        trace_id=trace_id or str(uuid4()),
        timestamp=time.time(),
        purpose=purpose,
        provider=provider,
        model=(structured_result.model if structured_result else None),
        locality=_provider_locality(provider),
        sequence=sequence,
        outcome=outcome,
        privacy_action=_privacy_action(provider, risk, privacy_result),
        is_retry=is_retry,
        is_fallback=is_fallback,
        metrics=(
            structured_result.metrics
            if structured_result
            else UsageMetrics()
        ),
    ))


def _try_providers(
    providers: list[str],
    prompt: str,
    risk: float,
    privacy_result=None,
    cancel_event: Event | None = None,
    *, provider_call=None, purpose=WorkloadPurpose.CONVERSATION, options=None,
):
    """
    Attempt providers in order and return the first successful response.

    The same privacy result is passed to every provider attempt so fallback
    calls do not lose the sanitisation context from the original prompt.
    """
    last_error = None
    output_started = False
    if options is not None and options.on_visible_text is not None:
        callback = options.on_visible_text
        def forward(text):
            nonlocal output_started
            output_started = True
            callback(text)
        options = replace(options, on_visible_text=forward)

    trace_id = request_trace_id()
    attempted_providers = set()

    for sequence, provider in enumerate(providers, start=1):
        if _is_cancelled(cancel_event):
            _debug_print(
                "[ROUTER] Request cancelled before next provider."
            )
            return None, "Request cancelled."

        _debug_print(f"[ROUTER] trying provider {provider}")

        response, error, skipped = _call_provider_with_health(
            provider,
            prompt,
            risk,
            privacy_result,
            cancel_event=cancel_event,
            trace_id=trace_id,
            sequence=sequence,
            is_retry=provider in attempted_providers,
            is_fallback=(
                bool(attempted_providers)
                and provider not in attempted_providers
            ),
            **({"provider_call": provider_call, "purpose": purpose} if provider_call is not None else {}),
            **({"options": options} if options is not None else {}),
        )
        attempted_providers.add(provider)

        if _is_cancelled(cancel_event):
            _debug_print(
                "[ROUTER] Request cancelled after provider attempt."
            )
            return None, "Request cancelled."

        if skipped:
            _debug_print(f"[ROUTER] {provider} skipped due to recent failure.")
            continue

        if error == "Request cancelled.":
            return None, error

        if error or not response:
            if output_started:
                # Replaying another answer after audible/visible output would
                # duplicate or contradict what the user has already received.
                return None, PARTIAL_STREAM_ERROR
            if error not in COMPLETION_ERRORS:
                logger.log_failure(provider)
            _debug_print(f"[ROUTER] {provider} failed: {error}")
            last_error = error
            continue

        _debug_print(f"[ROUTER] {provider} succeeded")
        return response, None

    return None, last_error


def _call_provider_with_health(
    provider: str,
    prompt: str,
    risk: float,
    privacy_result=None,
    cancel_event: Event | None = None,
    *,
    trace_id: str | None = None,
    sequence: int = 1,
    is_retry: bool = False,
    is_fallback: bool = False,
    provider_call=None,
    purpose=WorkloadPurpose.CONVERSATION,
    options=None,
):
    """
    Call one provider unless telemetry says it is temporarily unhealthy.

    This keeps cooldown handling in the router while llm_service handles
    conversation history and provider-specific rendering.
    """
    if logger.should_skip_provider(provider):
        _record_conversation_attempt(
            provider=provider,
            risk=risk,
            privacy_result=privacy_result,
            trace_id=trace_id,
            sequence=sequence,
            outcome=AttemptOutcome.COOLDOWN_SKIPPED,
            is_retry=is_retry,
            is_fallback=is_fallback,
            purpose=purpose,
        )
        _debug_print(
            f"[ROUTER] Skipping {provider} due to recent failure; using fallback."
        )
        return None, f"{provider} is temporarily unavailable after a recent failure.", True

    logger.log_attempt(provider)
    call = provider_call or llm_service.call_provider
    try:
        result = call(
            provider,
            prompt,
            risk=risk,
            privacy_result=privacy_result,
            cancel_event=cancel_event,
            **({"options": options} if options is not None else {}),
        )
        response, error = result
    except Exception:
        if provider_call is None:
            raise
        result = (None, "Tool interpretation provider failed.")
        response, error = result

    if _is_cancelled(cancel_event) or error == "Request cancelled.":
        outcome = AttemptOutcome.CANCELLED
    elif error:
        outcome = AttemptOutcome.FAILURE
    elif not response:
        outcome = AttemptOutcome.EMPTY_RESPONSE
    else:
        outcome = AttemptOutcome.SUCCESS

    _record_conversation_attempt(
        provider=provider,
        risk=risk,
        privacy_result=privacy_result,
        trace_id=trace_id,
        sequence=sequence,
        outcome=outcome,
        result=result,
        is_retry=is_retry,
        is_fallback=is_fallback,
        purpose=purpose,
    )

    return response, error, False


def _high_risk_unavailable_message():
    return (
        "I'm unable to process that request right now because my local AI provider is "
        "unavailable and the request appears to contain sensitive information."
    )


def _provider_unavailable_message():
    return (
        "I'm unable to process that request right now because my AI providers are "
        "unavailable."
    )


def _no_local_provider_configured_message():
    return (
        "No local AI provider is configured, so I can't process sensitive requests. "
        "Check CODA_LOCAL_PROVIDERS."
    )


def _no_provider_configured_message():
    return (
        "No AI providers are configured for this request. Check "
        "CODA_CLOUD_PROVIDERS and CODA_LOCAL_PROVIDERS."
    )


def _get_configured_provider_groups():
    cloud_providers = registry.resolve_provider_list(
        os.getenv("CODA_CLOUD_PROVIDERS", ""),
        provider_type="cloud",
    )

    local_providers = registry.resolve_provider_list(
        os.getenv("CODA_LOCAL_PROVIDERS", ""),
        provider_type="local",
    )

    return cloud_providers, local_providers


def get_provider_order(privacy_result):
    """
    Build the provider attempt order from privacy level and cloud policy.

    Float input is still supported for older tests/helpers, but normal routing
    now passes the full privacy result from analyze_privacy().
    """
    cloud_providers, local_providers = _get_configured_provider_groups()

    if isinstance(privacy_result, (int, float)):
        privacy_result = {
            "risk": float(privacy_result),
            "level": policy.get_risk_level(float(privacy_result)),
            "categories": [],
            "matches": [],
        }

    level = privacy_result["level"]
    cloud_action = policy.get_cloud_action(privacy_result)

    if level == "high":
        if cloud_action == policy.ACTION_BLOCK:
            return local_providers
        return local_providers + cloud_providers

    if level == "medium":
        if cloud_action == policy.ACTION_BLOCK:
            return local_providers
        return local_providers + cloud_providers

    return cloud_providers + local_providers


def route_request(
    prompt: str,
    cancel_event: Event | None = None,
    *, options=None,
):
    """
    Analyse privacy, choose provider order, and return the first good response.

    Routing decides whether cloud providers are allowed, while llm_service
    decides what cloud-safe content those providers actually receive.
    """
    if options is None:
        requested = conversation_options(prompt)
        # Inherited conversation behavior needs no override on a legacy call.
        if requested.thinking is not None or requested.max_tokens is not None:
            options = requested
    context = current_request()
    if context is not None and (context.speech_stream is not None or context.display_stream is not None):
        # Only ordinary conversation enters here. Classifiers, extractors and
        # tool interpretation keep their existing validation/redaction gates.
        def visible(text):
            if context.display_stream is not None:
                context.display_stream.feed(text)
            if context.speech_stream is not None:
                context.speech_stream.feed(text)
        options = replace(options or conversation_options(prompt), on_visible_text=visible)
    privacy_result = analyze_privacy(prompt)
    risk = privacy_result["risk"]

    providers = get_provider_order(privacy_result)

    _debug_print(f"[DEBUG - ROUTER] Privacy risk: {risk}")
    _debug_print(f"[DEBUG - ROUTER] Provider order: {providers}")

    # High risk, LOCAL ONLY when cloud policy blocks fallback
    if (
        privacy_result["level"] == "high"
        and policy.get_cloud_action(privacy_result) == policy.ACTION_BLOCK
    ):
        if not providers:
            _debug_print("[ROUTER] High risk -> no local providers configured.")
            return _no_local_provider_configured_message(), None

        _debug_print("[ROUTER] High risk -> using LOCAL providers only")

        response, error = _try_providers(
            providers,
            prompt,
            risk,
            privacy_result,
            cancel_event=cancel_event,
            **({"options": options} if options is not None else {}),
        )

        if response:
            return response, None

        if error == "Request cancelled." or error in COMPLETION_ERRORS:
            return None, error

        return _high_risk_unavailable_message(), None

    # Sensitive request, try providers in policy order
    if privacy_result["level"] in ("medium", "high"):
        if not providers:
            _debug_print("[ROUTER] Medium risk -> no providers configured.")
            return _no_provider_configured_message(), None

        _debug_print(f"[ROUTER] Medium risk -> trying providers in order: {providers}")

        response, error = _try_providers(
            providers,
            prompt,
            risk,
            privacy_result,
            cancel_event=cancel_event,
            **({"options": options} if options is not None else {}),
        )

        if response:
            return response, None

        if error == "Request cancelled." or error in COMPLETION_ERRORS:
            return None, error

        _debug_print("[ROUTER] All providers failed -> giving up")
        return _provider_unavailable_message(), None

    # Low risk, CLOUD first, fallback to LOCAL
    if not providers:
        _debug_print("[ROUTER] Low risk -> no providers configured.")
        return _no_provider_configured_message(), None

    _debug_print(f"[DEBUG] Primary provider being checked: {providers[0]}")
    _debug_print(f"[ROUTER] Low risk -> trying providers in order: {providers}")

    response, error = _try_providers(
        providers,
        prompt,
        risk,
        privacy_result,
        cancel_event=cancel_event,
        **({"options": options} if options is not None else {}),
    )

    if response:
        return response, None

    if error == "Request cancelled." or error in COMPLETION_ERRORS:
        return None, error

    _debug_print("[ROUTER] All providers failed -> giving up")
    return _provider_unavailable_message(), None
