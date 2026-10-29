"""Bounded speech copies of incremental answer text; never owns semantic history."""
import re
import time

from tts.segmentation import _BOUNDARY_PATTERN, _NON_TERMINAL_ABBREVIATIONS


class SpeakableChunks:
    """Prefer sentences, then complete words at phrase/size/elapsed boundaries.

    Producers call feed, consumers may call poll while waiting for more tokens.
    A trailing lexical token is retained until whitespace proves it complete.
    Oversized unbroken tokens and fenced code are omitted from speech rather
    than cut into misleading numbers, URLs or identifiers. The caller retains
    the exact semantic response independently.
    """
    def __init__(self, *, max_chars=320, max_buffer=1024, min_chars=40,
                 delay_seconds=1.0, clock=time.monotonic):
        if not 1 <= min_chars <= max_chars <= max_buffer or delay_seconds <= 0:
            raise ValueError("Invalid incremental speech bounds.")
        self.max_chars = max_chars
        self.max_buffer = max_buffer
        self.min_chars = min_chars
        self.delay_seconds = delay_seconds
        self.clock = clock
        self.buffer = ""
        self._since = clock()
        self._ticks = 0
        self._fenced = False
        self._discard_token = False
        self._closed = False

    def feed(self, text):
        if self._closed:
            raise ValueError("Speech accumulator is closed.")
        chunks = []
        for char in text:
            if char == '`':
                self._ticks += 1
                if self._ticks == 3:
                    self._fenced = not self._fenced
                    self._ticks = 0
                    if self._fenced:
                        self.buffer += " Code omitted from speech. "
                continue
            # Single/double backticks are presentation markup only.
            self._ticks = 0
            if self._fenced:
                continue
            if self._discard_token:
                if not char.isspace():
                    continue
                self._discard_token = False
            self.buffer += char
            if len(self.buffer) >= self.max_buffer:
                # Flush complete words first; bound an unbroken token without
                # ever pronouncing an arbitrary prefix as a factual value.
                boundary = self.buffer.rfind(' ')
                if boundary > 0 and '[' not in self.buffer[:boundary]:
                    chunks.extend(self._take(boundary + 1))
                else:
                    self.buffer = ""
                    self._discard_token = True
                    chunks.append("Long technical text omitted from speech.")
            chunks.extend(self.poll())
        return chunks

    def poll(self):
        if not self.buffer.strip():
            return []
        # Never separate Markdown link labels from their target or incomplete
        # formatting. A complete link is rendered only when the chunk leaves.
        if self.buffer.count('[') > self.buffer.count(']') or re.search(r'\]\([^)]*$', self.buffer):
            return []
        for match in _BOUNDARY_PATTERN.finditer(self.buffer):
            prefix = self.buffer[:match.end()]
            following = self.buffer[match.end():].lstrip().lstrip("\"'([")
            if not following or not (following[0].isupper() or following[0].isdigit()):
                continue
            if prefix.lower().endswith(_NON_TERMINAL_ABBREVIATIONS):
                continue
            preceding = self.buffer[match.start()-1:match.start()]
            if match.group().startswith('.') and preceding.isupper():
                continue
            return self._take(match.end())
        timed_out = self.clock() - self._since >= self.delay_seconds
        if len(self.buffer) < self.max_chars and not timed_out:
            return []
        # Last whitespace leaves the final token intact, including partial
        # punctuation, decimals, versions, URLs and long identifiers.
        boundary = min(len(self.buffer), self.max_chars)
        candidates = [m.end() for m in re.finditer(r'\s+', self.buffer[:boundary])]
        if not candidates or candidates[-1] < self.min_chars:
            return []
        boundary = candidates[-1]
        phrase = list(re.finditer(r'[,;:]\s+', self.buffer[:boundary]))
        if phrase and phrase[-1].end() >= self.min_chars:
            boundary = phrase[-1].end()
        prefix = self.buffer[:boundary]
        if prefix.lower().rstrip().endswith(_NON_TERMINAL_ABBREVIATIONS):
            return []
        return self._take(boundary)

    def finish(self):
        if self._closed:
            return []
        self._closed = True
        return self._take(len(self.buffer))

    def _take(self, count):
        text, self.buffer = self.buffer[:count], self.buffer[count:]
        self._since = self.clock()
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
        text = re.sub(r'(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|>\s*)', '', text)
        text = text.replace('**', '').replace('__', '').strip()
        return [text] if text else []
