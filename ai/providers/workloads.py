"""Operator-configurable thinking policy for application-owned LLM workloads."""

import os
import re

from ai.providers.generation import GenerationOptions, Workload


def options_for(workload):
    """Routine work opts out; conversation inherits; deliberate reasoning opts in."""
    default = (None if workload == Workload.CONVERSATION else workload == Workload.REASONING)
    configured = os.getenv(f"CODA_THINKING_{workload.name}", "").strip().lower()
    values = {"on": True, "true": True, "1": True, "off": False, "false": False,
              "0": False, "auto": None, "inherit": None}
    return GenerationOptions(workload, thinking=values.get(configured, default))


def explicit_reasoning_request(message):
    """Only an explicit user prefix selects analysis, never embedded tool data."""
    return bool(re.match(
        r"^\s*(?:think (?:carefully|step by step)\b|reason (?:carefully|step by step)\b|"
        r"analy[sz]e in depth\b|reason:)", message, re.IGNORECASE,
    ))


def conversation_options(message):
    return options_for(Workload.REASONING if explicit_reasoning_request(message) else Workload.CONVERSATION)
