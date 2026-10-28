"""Bounded local tool diagnostics, separate from speech and model history."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

from ai.tools.error_feedback import safe_diagnostic


_LOGGER = logging.getLogger("coda.tools")
_LOCK = Lock()
LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "mcp" / "failures.log"


def record_tool_failure(tool_name, display):
    """Persist sanitized failure evidence; a logging failure cannot fail a tool."""
    try:
        with _LOCK:
            if not _LOGGER.handlers:
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(LOG_PATH, maxBytes=256000, backupCount=2, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                _LOGGER.addHandler(handler)
                _LOGGER.propagate = False
            _LOGGER.warning("%s: %s", safe_diagnostic(tool_name, max_chars=128), safe_diagnostic(display))
    except (OSError, ValueError):
        pass
