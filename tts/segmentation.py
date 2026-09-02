"""Conservative sentence segmentation for complete TTS responses."""

import re

_BOUNDARY_PATTERN = re.compile(r"""[.!?]+['")\]]*(?=\s+|$)""")
_NON_TERMINAL_ABBREVIATIONS = (
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "prof.",
    "e.g.",
    "i.e.",
)


def split_spoken_text(text: str) -> list[str]:
    """Split text only at boundaries that are safe for TTS recovery."""
    segments = []
    segment_start = 0

    for match in _BOUNDARY_PATTERN.finditer(text):
        if match.end() == len(text):
            continue

        prefix = text[segment_start:match.end()]
        following = text[match.end():].lstrip()
        following_word = following.lstrip("\"'([")

        if not following_word:
            continue

        if not (following_word[0].isupper() or following_word[0].isdigit()):
            continue

        if prefix.lower().endswith(_NON_TERMINAL_ABBREVIATIONS):
            continue

        preceding_character = text[match.start() - 1]
        if match.group().startswith(".") and preceding_character.isupper():
            continue
        
        segment = prefix.strip()
        if segment:
            segments.append(segment)
            
        segment_start = match.end()
        
    final_segment = text[segment_start:].strip()
    if final_segment:
        segments.append(final_segment)
        
    return segments
