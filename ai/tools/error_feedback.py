"""Interpret failure data without promoting server text into trusted instructions."""

import re

from ai.privacy.sanitizer import sanitize_text


# These are result codes, not guesses based on words appearing in a traceback.
ERROR_EXPLANATIONS = {
    "outside_allowed_roots": "I couldn't access that directory because it isn't in my allowed locations. You can change this in the filesystem configuration.",
    "blocked_path": "I couldn't access that location because it is blocked in the filesystem configuration.",
    "permission_denied": "The tool doesn't have permission to access that resource.",
    "not_found": "The requested resource could not be found.",
    "not_a_directory": "That location isn't a directory.",
    "configuration_error": "The tool's configuration is missing or invalid. Please check its configuration.",
    "invalid_arguments": "The tool arguments are invalid or incomplete.",
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
    if not message and isinstance(blocks, list):
        message = "\n".join(
            block["text"] for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    return code, message


def spoken_error(result):
    """Speak only known reasons; keep arbitrary text/path/tracebacks in diagnostics."""
    error = result.error
    if error is None:
        return "The tool failed, but it didn't provide a reason."
    if error.code != "mcp_tool_error":
        return error.message
    code, details = server_error(result)
    if code in ERROR_EXPLANATIONS:
        return ERROR_EXPLANATIONS[code]
    # The SDK wraps a server exception in text. Only recognize explicit types,
    # never incidental mentions of permission, files or timeouts in prose.
    exception = re.search(r"(?:^|:\s)(PermissionError|FileNotFoundError|NotADirectoryError):", details)
    if exception:
        return {
            "PermissionError": ERROR_EXPLANATIONS["permission_denied"],
            "FileNotFoundError": ERROR_EXPLANATIONS["not_found"],
            "NotADirectoryError": ERROR_EXPLANATIONS["not_a_directory"],
        }[exception[1]]
    return "The tool reported a failure. I couldn't determine the reason. Details are available on screen."


def error_display(result):
    """Preserve supplied detail for visual diagnostics without speaking it."""
    code, details = server_error(result)
    normalized = result.error.code if result.error else "tool_error"
    message = result.error.message if result.error else "The tool failed."
    heading = f"{message} [{normalized}" + (f"/{code}" if code else "") + "]"
    return safe_diagnostic(heading + ("\nServer details: " + details if details else ""))
