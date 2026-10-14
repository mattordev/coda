"""Readable tool feedback, separate from protocol data and model context."""

import json

from ai.privacy.sanitizer import sanitize_text
from ai.tools.models import ToolResult


def _readable(value, *, depth=0):
    if depth > 6 or value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _readable(json.loads(text), depth=depth + 1)
            except (ValueError, RecursionError):
                pass
        return text
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(filter(None, (_readable(item, depth=depth + 1) for item in value[:20])))
    if isinstance(value, dict):
        if len(value) == 1:
            return _readable(next(iter(value.values())), depth=depth + 1)
        parts = []
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                break
            text = _readable(item, depth=depth + 1)
            if text:
                label = str(key).replace("_", " ").capitalize()
                parts.append(f"{label}: {text}")
        return "; ".join(parts)
    return ""


def prepare_tool_response(result: ToolResult, *, max_chars: int = 1200) -> str:
    """Prefer server text, fall back to structured values, never speak metadata."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("Tool response limit must be a positive integer.")
    if not result.success:
        text = result.error.message if result.error else "The tool failed."
    else:
        data = result.data
        if isinstance(data, dict) and ("content" in data or "structuredContent" in data):
            blocks = data.get("content")
            parts = []
            if isinstance(blocks, list):
                for block in blocks[:20]:
                    if isinstance(block, dict) and block.get("type") == "text":
                        part = _readable(block.get("text"))
                        if part:
                            parts.append(part)
            text = "\n".join(parts) or _readable(data.get("structuredContent"))
        elif isinstance(data, dict) and any(key in data for key in ("_meta", "isError", "resultType")):
            text = ""
        else:
            text = _readable(data)
        text = text or "Tool completed."
    text = sanitize_text(text)
    if len(text) > max_chars:
        return text[:max_chars - 1].rstrip() + "…"
    return text
