"""Operator-configurable thinking policy for application-owned LLM workloads."""

import os
import re
import json

from ai.providers.generation import GenerationOptions, Workload


def options_for(workload):
    """Routine work opts out; conversation inherits; deliberate reasoning opts in."""
    default = (None if workload == Workload.CONVERSATION else workload == Workload.REASONING)
    configured = os.getenv(f"CODA_THINKING_{workload.name}", "").strip().lower()
    values = {"on": True, "true": True, "1": True, "off": False, "false": False,
              "0": False, "auto": None, "inherit": None}
    defaults = {Workload.CLASSIFICATION: 256, Workload.EXTRACTION: 2048,
                Workload.INTERPRETATION: 512}
    budget = defaults.get(workload)
    configured_budget = os.getenv(f"CODA_MAX_TOKENS_{workload.name}", "").strip()
    if configured_budget:
        try:
            candidate = int(configured_budget)
            if candidate > 0:
                budget = candidate
        except ValueError:
            pass
    return GenerationOptions(workload, thinking=values.get(configured, default), max_tokens=budget)


def classification_options(messages):
    """Reserve a bounded larger budget when tool arguments may copy long input.

    JSON-escaped UTF-8 bytes plus envelope space conservatively estimate output
    size without pretending to know the selected model's tokenizer. Catalogue
    text cannot enlarge the allowance: only the user message counts.
    """
    routine = options_for(Workload.CLASSIFICATION)
    try:
        payload = json.loads(messages[-1]["content"])
        structured = any(isinstance(item.get("input_schema"), dict)
                         for item in payload["available_intents"])
        estimate = len(json.dumps(payload["message"], ensure_ascii=False).encode("utf-8")) + 128
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return routine
    if structured and estimate > routine.max_tokens:
        extraction = options_for(Workload.EXTRACTION)
        if extraction.max_tokens > routine.max_tokens:
            return extraction
    return routine


def explicit_reasoning_request(message):
    """Only an explicit user prefix selects analysis, never embedded tool data."""
    return bool(re.match(
        r"^\s*(?:think (?:carefully|step by step)\b|reason (?:carefully|step by step)\b|"
        r"analy[sz]e in depth\b|reason:)", message, re.IGNORECASE,
    ))


def conversation_options(message):
    return options_for(Workload.REASONING if explicit_reasoning_request(message) else Workload.CONVERSATION)
