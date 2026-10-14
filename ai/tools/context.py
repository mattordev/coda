"""Explicit, bounded conversational representations of full tool results."""

import json

from ai.privacy.detector import analyze_privacy
from ai.privacy import policy
from ai.privacy.sanitizer import sanitize_structure
from ai.tools.models import ToolResult


def prepare_tool_context(
    result: ToolResult, *, for_cloud: bool, max_chars: int = 2000,
) -> str | None:
    """Apply privacy to the full envelope before bounding a separate representation."""
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise ValueError("Tool context limit must be a positive integer.")
    envelope = {
        "success": result.success,
        "data": result.data,
        "error": None if result.error is None else {
            "code": result.error.code, "message": result.error.message,
        },
    }
    try:
        text = json.dumps(envelope, ensure_ascii=False, allow_nan=False)
        if for_cloud:
            action = policy.get_cloud_action(analyze_privacy(text))
            if action == policy.ACTION_BLOCK:
                return None
            if action == policy.ACTION_SUMMARIZE:
                text = "The tool succeeded." if result.success else "The tool failed."
            elif action == policy.ACTION_SANITIZE:
                text = json.dumps(sanitize_structure(envelope), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None
    return text[:max_chars]
