"""Interpret failure data without promoting server text into trusted instructions."""

import re

from ai.privacy.sanitizer import sanitize_text


# These are result codes, not guesses based on words appearing in a traceback.
ERROR_EXPLANATIONS = {
    "outside_allowed_roots": "I couldn't access that directory because it isn't in my allowed locations. You can change this in the filesystem configuration.",
    "blocked_path": "I couldn't access that location because it is blocked in the filesystem configuration.",
    "permission_denied": "I don't have permission to access that.",
    "not_found": "Sorry, I couldn't find that resource.",
    "file_not_found": "The requested file could not be found.",
    "is_a_directory": "That location is a directory. Please choose a file to read.",
    "invalid_encoding": "I couldn't read that file as UTF-8 text.",
    "file_too_large": "That file is too large for this read tool. Please open it manually.",
    "not_a_directory": "That location isn't a directory.",
    "configuration_error": "The tool's configuration is missing or invalid. Please check its configuration.",
    "invalid_arguments": "I couldn't use those details. Please check the required information and try again.",
    "workspace_ambiguous": "More than one accessible match was found. Please give me a more specific location.",
    "workspace_required": "Please provide an absolute path or configure a workspace first.",
    "workspace_not_found": "Sorry, I couldn't find an accessible match. Please provide a more specific location.",
    "workspace_unsafe": "I couldn't safely access that location. Please provide an authorized absolute path.",
    "workspace_limit": "There are too many entries to resolve that reference. Please provide an absolute path.",
    "workspace_changed": "The filesystem settings changed. Please request the action again.",
    "workspace_unavailable": "I couldn't access that workspace. Please provide an absolute path or check its settings.",
}

FAILURE_CATEGORIES = {
    "no_results": "no_results", "not_found": "not_found", "file_not_found": "not_found",
    "workspace_not_found": "not_found", "permission_denied": "permission_denied",
    "outside_allowed_roots": "permission_denied", "blocked_path": "permission_denied",
    "authentication_error": "permission_denied", "timeout": "timeout",
    "connection_error": "unavailable", "connection_backoff": "unavailable",
    "server_disabled": "unavailable", "server_not_found": "unavailable",
    "tool_not_found": "unavailable", "unavailable": "unavailable",
    "configuration_error": "unavailable", "cleanup_timeout": "unavailable",
    "invalid_arguments": "invalid_arguments", "invalid_request": "invalid_arguments",
    "is_a_directory": "invalid_arguments", "not_a_directory": "invalid_arguments",
    "workspace_ambiguous": "invalid_arguments", "workspace_required": "invalid_arguments",
    "workspace_unsafe": "invalid_arguments", "workspace_limit": "invalid_arguments",
    "workspace_changed": "unavailable", "workspace_unavailable": "unavailable",
    "cancelled": "cancelled",
}
FAILURE_RESPONSES = {
    "no_results": "Sorry, I couldn't get any results for that request.",
    "not_found": "Sorry, I couldn't find what you requested.",
    "permission_denied": "I don't have permission to access that.",
    "timeout": "That took a little too long, so I couldn't finish the request.",
    "unavailable": "Sorry, that service is unavailable right now.",
    "invalid_arguments": ERROR_EXPLANATIONS["invalid_arguments"],
    "cancelled": "That request was cancelled.",
    "unknown_error": "Sorry, something went wrong while I was trying to do that.",
}
TYPED_REASONS = {
    "PermissionError": "permission_denied", "FileNotFoundError": "file_not_found",
    "NotADirectoryError": "not_a_directory", "TimeoutError": "timeout",
    "ConnectionError": "unavailable",
}


def safe_diagnostic(text, *, max_chars=12000):
    """Redact known secrets and terminal controls before displaying/logging data."""
    text = sanitize_text(str(text))
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text if len(text) <= max_chars else text[:max_chars - 1] + "…"


def server_error(result):
    """Return a machine code and details; accept both structured and text errors."""
    if not isinstance(result.data, dict):
        return None, ""
    structured = result.data.get("structuredContent")
    if not isinstance(structured, dict):
        structured = result.data
    error = structured.get("error")
    code, message = None, ""
    if isinstance(error, dict):
        code = error.get("code") if isinstance(error.get("code"), str) else None
        message = error.get("message") if isinstance(error.get("message"), str) else ""
    blocks = result.data.get("content")
    if isinstance(blocks, list):
        text = "\n".join(
            block["text"] for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if text and text != message:
            message = message + "\n" + text if message else text
    diagnostic = result.data.get("diagnostic")
    if isinstance(diagnostic, dict) and isinstance(diagnostic.get("exception_type"), str):
        message += "\nException type: " + diagnostic["exception_type"]
    return code, message


def _failure_reason(result):
    """Prefer host codes, then allowlisted server claims and explicit SDK types."""
    if result.error is not None and result.error.code != "mcp_tool_error":
        return result.error.code
    code, details = server_error(result)
    if code is not None:
        return code
    # The SDK wraps a server exception in text. Only recognize explicit types,
    # never incidental mentions of permission, files or timeouts in prose.
    exception = re.search(r"(?:^|:\s)(PermissionError|FileNotFoundError|NotADirectoryError|TimeoutError|ConnectionError):", details)
    if exception:
        return TYPED_REASONS[exception[1]]
    return "unknown_error"


def failure_category(result):
    """Classify a failure without changing its normalized code or original data."""
    return FAILURE_CATEGORIES.get(_failure_reason(result), "unknown_error")


def spoken_error(result, *, question=""):
    """Use fixed conversational wording; never quote an arbitrary error message."""
    reason = _failure_reason(result)
    category = FAILURE_CATEGORIES.get(reason, "unknown_error")
    structured = result.data.get("structuredContent") if isinstance(result.data, dict) else None
    search = (isinstance(structured, dict) and structured.get("kind") == "web_search") or bool(
        re.search(r"\bsearch\b|\bsearch_web\b", question, re.IGNORECASE)
    )
    if search and category in ("no_results", "unavailable"):
        return "Sorry, I couldn't get any results for that search."
    if reason == "not_found" and re.search(r"\bfile\b|\bread_file\b", question, re.IGNORECASE):
        return "Sorry, I couldn't find that file."
    return ERROR_EXPLANATIONS.get(reason, FAILURE_RESPONSES[category])


def error_display(result):
    """Preserve supplied detail for visual diagnostics without speaking it."""
    code, details = server_error(result)
    normalized = result.error.code if result.error else "tool_error"
    message = result.error.message if result.error else "The tool failed."
    heading = f"{message} [{normalized}" + (f"/{code}" if code else "") + "]"
    heading += f"\nFailure category: {failure_category(result)}"
    return safe_diagnostic(heading + ("\nServer details: " + details if details else ""))
