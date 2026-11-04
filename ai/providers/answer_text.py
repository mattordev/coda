"""Keep unsupported model tool protocols out of human-facing text streams."""
from ai.providers.completion import UNSUPPORTED_COMPLETION_ERROR


_MARKERS = ('<|tool_call_start|>', '<|tool_call_end|>', '<tool_call>', '</tool_call>')


class UnsupportedToolOutput(ValueError):
    """A model proposed an unsupported tool protocol instead of answer text."""


class AnswerTextGuard:
    """Hold only possible marker suffixes, including markers split across deltas."""

    def __init__(self, emit):
        self._emit = emit
        self._pending = ''
        self.failed = False

    def validate(self, text):
        if self.failed or any(marker in text.casefold() for marker in _MARKERS):
            self.failed = True
            raise UnsupportedToolOutput(UNSUPPORTED_COMPLETION_ERROR)

    def feed(self, text):
        text = self._pending + text
        self.validate(text)
        lowered = text.casefold()
        hold = max((length for length in range(1, min(len(text), max(map(len, _MARKERS)) - 1) + 1)
                    if any(marker.startswith(lowered[-length:]) for marker in _MARKERS)), default=0)
        self._pending = text[-hold:] if hold else ''
        visible = text[:-hold] if hold else text
        if visible:
            self._emit(visible)

    def finish(self):
        # A truncated control token is not a readable answer either. A bare
        # '<' remains valid prose/code and must not be dropped.
        if self.failed or self._pending.casefold().startswith(('<|', '<tool', '</tool')):
            self.failed = True
            raise UnsupportedToolOutput(UNSUPPORTED_COMPLETION_ERROR)
        if self._pending:
            self._emit(self._pending)
            self._pending = ''
