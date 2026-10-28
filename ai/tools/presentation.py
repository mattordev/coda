"""Readable tool feedback, separate from protocol data and model context."""

import json

from ai.privacy.sanitizer import sanitize_text
from ai.tools.models import ToolResult
from ai.tools.error_feedback import error_display, safe_diagnostic, spoken_error
from ai.tools.result_summary import summarize_result


def _readable(value, *, depth=0, max_items=20):
    if depth > 6 or value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _readable(json.loads(text), depth=depth + 1, max_items=max_items)
            except (ValueError, RecursionError):
                pass
        return text
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(filter(None, (_readable(item, depth=depth + 1, max_items=max_items) for item in value[:max_items])))
    if isinstance(value, dict):
        if len(value) == 1:
            return _readable(next(iter(value.values())), depth=depth + 1, max_items=max_items)
        parts = []
        for index, (key, item) in enumerate(value.items()):
            if max_items is not None and index >= max_items:
                break
            text = _readable(item, depth=depth + 1, max_items=max_items)
            if text:
                label = str(key).replace("_", " ").capitalize()
                parts.append(f"{label}: {text}")
        return "; ".join(parts)
    return ""


def prepare_tool_response(result: ToolResult, *, max_chars: int = 1200, question: str = "") -> str:
    """Prefer server text, fall back to structured values, never speak metadata."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("Tool response limit must be a positive integer.")
    if not result.success:
        text = spoken_error(result)
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
        text = summarize_result(result.data, text, question) or text or "Tool completed."
    text = sanitize_text(text)
    if len(text) > max_chars:
        return text[:max_chars - 1].rstrip() + "…"
    return text


def prepare_tool_display(result: ToolResult, *, max_chars: int = 12000) -> str:
    """Detailed, sanitized visual output, independent of the speech budget."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("Tool display limit must be a positive integer.")
    if not result.success:
        text = error_display(result)
    else:
        data = result.data
        if isinstance(data, dict) and ("content" in data or "structuredContent" in data):
            blocks = data.get("content")
            parts = [
                _readable(block.get("text"), max_items=None)
                for block in blocks if isinstance(block, dict) and block.get("type") == "text"
            ] if isinstance(blocks, list) else []
            text = "\n".join(filter(None, parts)) or _readable(data.get("structuredContent"), max_items=None)
        elif isinstance(data, dict) and any(key in data for key in ("_meta", "isError", "resultType")):
            text = ""
        else:
            text = _readable(data, max_items=None)
        text = text or "Tool completed."
    return safe_diagnostic(text, max_chars=max_chars)


def prepare_full_tool_output(result: ToolResult, *, max_chars=8 * 1024 * 1024):
    """Explicit diagnostic access to the complete normalized envelope, redacted."""
    try:
        text = json.dumps({
            "success": result.success, "data": result.data,
            "error": {"code": result.error.code, "message": result.error.message} if result.error else None,
        }, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return "The retained result cannot be represented as JSON."
    return safe_diagnostic(text, max_chars=max_chars)
