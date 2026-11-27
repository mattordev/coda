"""Bounded capture for a subprocess stdout or stderr pipe."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ProcessOutput:
    text: str
    dropped_bytes: int


def capture_output(stream, limit, secrets=()):
    """Drain a binary pipe through EOF, retaining only a bounded prefix."""
    prefix = bytearray()
    dropped = 0
    while chunk := stream.read(8192):
        keep = min(len(chunk), limit - len(prefix))
        prefix.extend(chunk[:keep])
        dropped += len(chunk) - keep

    text = prefix.decode("utf-8", errors="replace")
    values = tuple(sorted({value for value in secrets if value}, key=len, reverse=True))
    if values:
        # Truncation can cut a secret before a complete match is possible.
        # Include only the longest matching partial suffix in the redaction.
        # A full match still wins over a partial match at the same position.
        retained = bytes(prefix)
        partial = []
        if dropped:
            for value in values:
                encoded = value.encode("utf-8")
                for size in range(min(len(retained), len(encoded) - 1), 0, -1):
                    if retained.endswith(encoded[:size]):
                        partial.append(encoded[:size])
                        break
        patterns = [re.escape(value.encode("utf-8")) for value in values]
        if partial:
            patterns.append(re.escape(max(partial, key=len)) + rb"\Z")
        text = re.sub(b"|".join(patterns), b"[redacted]", retained).decode("utf-8", errors="replace")
    return ProcessOutput(text, dropped)
