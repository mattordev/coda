"""Request-scoped generation controls; never change a provider's global defaults."""

from dataclasses import dataclass, replace
from enum import Enum
import inspect
from threading import Event
from typing import Callable


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
    on_visible_text: Callable[[str], None] | None = None

    def __post_init__(self):
        if self.on_visible_text is not None and not callable(self.on_visible_text):
            raise ValueError("on_visible_text must be callable or None.")
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
    closed = Event()
    if options is not None and options.on_visible_text is not None and "options" in kwargs:
        def forward(text):
            if closed.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise InterruptedError("Request cancelled.")
            options.on_visible_text(text)
        kwargs["options"] = replace(options, on_visible_text=forward)
    try:
        return generate(messages, **kwargs)
    finally:
        # Timed-out adapters may have an abandoning worker. It must not emit
        # late text into a subsequent runtime request.
        closed.set()


def emit_visible_text(options, text, cancel_event=None):
    """Adapters call this only for answer content, never reasoning channels.

    The callback runs synchronously: consumers can apply bounded backpressure.
    Exceptions abort this attempt; providers must never retry the callback.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Request cancelled.")
    if isinstance(text, str) and text and options is not None and options.on_visible_text is not None:
        options.on_visible_text(text)
