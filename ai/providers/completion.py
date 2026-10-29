"""Completion checks are safety decisions, independent of optional telemetry."""

OUTPUT_LIMIT_ERROR = "The AI response reached its output limit. Please narrow the request or increase its token budget."
INCOMPLETE_STREAM_ERROR = "The AI response ended before completion. Please try again."
UNSUPPORTED_COMPLETION_ERROR = "The AI response did not finish with a usable text answer."
COMPLETION_ERRORS = (OUTPUT_LIMIT_ERROR, INCOMPLETE_STREAM_ERROR, UNSUPPORTED_COMPLETION_ERROR)


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


class CompletionState:
    """Never accept length-terminated JSON merely because it happens to parse."""

    def __init__(self, options=None):
        self.required = options is not None and options.max_tokens is not None
        self.terminal = False
        self.error = None

    def observe_reason(self, reason):
        if reason is None:
            return
        self.terminal = True
        if reason in ("length", "max_tokens", "max_output_tokens"):
            self.error = OUTPUT_LIMIT_ERROR
        elif reason != "stop" and self.error is None:
            self.error = UNSUPPORTED_COMPLETION_ERROR

    def observe_openai(self, chunk):
        choices = _field(chunk, "choices", [])
        if choices:
            self.observe_reason(_field(choices[0], "finish_reason"))

    def observe_ollama(self, chunk):
        self.observe_reason(chunk.get("done_reason"))
        if chunk.get("done") is True:
            self.terminal = True

    def validate(self):
        if self.error:
            raise ValueError(self.error)
        if self.required and not self.terminal:
            raise ValueError(INCOMPLETE_STREAM_ERROR)
