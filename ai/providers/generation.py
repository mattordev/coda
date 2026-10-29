"""Request-scoped generation controls; never change a provider's global defaults."""

from dataclasses import dataclass
from enum import Enum
import inspect


class Workload(str, Enum):
    """The application operation, independent of a particular provider API."""

    CONVERSATION = "conversation"
    CLASSIFICATION = "intent_classification"
    EXTRACTION = "argument_extraction"
    INTERPRETATION = "tool_interpretation"
    REASONING = "reasoning"


@dataclass(frozen=True)
class GenerationOptions:
    """None means inherit provider/model behavior, rather than force a value."""

    workload: Workload = Workload.CONVERSATION
    thinking: bool | None = None
    max_tokens: int | None = None

    def __post_init__(self):
        if not isinstance(self.workload, Workload):
            raise ValueError("workload must be a Workload.")
        if self.thinking is not None and not isinstance(self.thinking, bool):
            raise ValueError("thinking must be a boolean or None.")
        if self.max_tokens is not None and (
            isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int)
            or self.max_tokens <= 0
        ):
            raise ValueError("max_tokens must be a positive integer or None.")


def call_generation(generate, messages, *, cancel_event=None, options=None):
    """Pass controls to capable adapters without replaying failed legacy calls.

    Inspect before the call: catching TypeError and retrying could repeat a real
    provider request whose implementation raised TypeError after accepting it.
    """
    if options is not None and not isinstance(options, GenerationOptions):
        raise TypeError("options must be GenerationOptions or None.")
    kwargs = {"cancel_event": cancel_event}
    if options is not None:
        try:
            parameters = inspect.signature(generate).parameters.values()
            capable = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                or (parameter.name == "options" and parameter.kind != inspect.Parameter.POSITIONAL_ONLY)
                for parameter in parameters
            )
        except (TypeError, ValueError):
            capable = False
        if capable:
            kwargs["options"] = options
    return generate(messages, **kwargs)
