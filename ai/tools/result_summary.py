"""Deterministic summaries of returned data; no model calls or tool execution."""

import json
from math import isfinite
import re

from ai.privacy.sanitizer import sanitize_text


_SPOKEN_EXTENSIONS = {"md": "markdown", "json": "jay son"}


def _payload(data):
    if isinstance(data, dict) and data.get("structuredContent") is not None:
        return data["structuredContent"]
    return data


def _directory_entries(data, text):
    payload = _payload(data)
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        entries = payload["entries"]
        if payload.get("kind") == "directory_listing" or (entries and all(
            isinstance(entry, dict) and entry.get("type") in ("file", "directory", "dir")
            for entry in entries
        )):
            if all(isinstance(entry, dict) and isinstance(entry.get("name"), str)
                   and entry.get("type") in ("file", "directory", "dir") for entry in entries):
                return [dict(entry, type="directory" if entry["type"] == "dir" else entry["type"])
                        for entry in entries], payload.get("truncated") is True
    # The speech renderer may limit content blocks; counts must use every
    # returned listing block, not just the excerpt prepared for reading.
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        text = "\n".join(block["text"] for block in data["content"]
                         if isinstance(block, dict) and block.get("type") == "text"
                         and isinstance(block.get("text"), str))
    lines = text.splitlines()
    if lines and all(re.fullmatch(r"\[(FILE|DIR)\] .+", line) for line in lines):
        return [{"name": line.split(" ", 1)[1], "type": "file" if line.startswith("[FILE]") else "directory"}
                for line in lines], False
    return None


def _names(entries, limit):
    # Redact before transforming punctuation so secrets remain recognizable.
    # Filename dots must be words for TTS, rather than sentence-ending pauses.
    names = []
    for entry in entries[:limit]:
        original = sanitize_text(entry["name"])
        name = original[:80].replace("\n", " ")
        stem, dot, extension = name.rpartition(".")
        if stem and entry["type"] == "file" and len(original) <= 80:
            name = stem + dot + _SPOKEN_EXTENSIONS.get(extension.casefold(), extension)
        name = re.sub(r"\s+", " ", name.replace(".", " dot ")).strip()
        names.append('"' + name + '"')
    return ", ".join(names)


def _question_text(question):
    """Explicit JSON arguments and path names are data, not presentation requests."""
    question = question.casefold().strip()
    if re.match(r"mcp\.[\w.-]+\s*\{", question):
        return ""
    question = re.sub(r'''(["'])[^"']*[\\/][^"']*\1''', "", question)
    return re.sub(r"(?:[a-z]:[\\/]|/|~[\\/])\S+", "", question)


def _presentation_style(question):
    """Select presentation independently from extracting directory facts."""
    if re.search(r"\b(?:recent(?:ly)?|latest|newest|last\s+(?:edited|modified|changed))\b(?!\.)", question):
        return "recent_files"
    if re.search(r"\b(?:all|every|exact)\s+(?:the\s+)?(?:file\s*names?|files?|entries|folders|directories|names)\b|"
                 r"\b(?:read|list|say)\s+(?:out\s+)?(?:the\s+)?(?:file\s*names?|names|everything)\b", question):
        return "all_names"
    return "overview"


def _valid_modified_at(entry):
    value = entry.get("modified_at")
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        isinstance(value, int) or isfinite(value)
    )


def summarize_result(data, text, question):
    """Return grounded speech for known shapes, or None for natural short text."""
    directory = _directory_entries(data, text)
    question = _question_text(question)
    if directory is not None:
        entries, partial = directory
        if not entries:
            return "No accessible entries were returned." if partial else "There are no accessible entries in that directory."
        # Answer an explicit filename question from the returned entries only.
        specific = [entry for entry in entries if re.search(
            r"(?<![\w.])" + re.escape(entry["name"].casefold()) + r"(?![\w.])", question
        )] if re.search(r"\b(is|does|find|contain|has|have)\b", question) else []
        if specific:
            return "The listing includes " + _names(specific, 3) + "."
        files = sum(entry["type"] == "file" for entry in entries)
        directories = len(entries) - files
        counts = []
        if directories:
            counts.append(f"{directories} " + ("directory" if directories == 1 else "directories"))
        if files:
            counts.append(f"{files} " + ("file" if files == 1 else "files"))
        prefix = "The returned portion contains " if partial else "The listing contains "
        counts_text = prefix + " and ".join(counts) + "."
        if re.search(r"\b(how many|count|number of)\b", question):
            return counts_text
        style = _presentation_style(question)
        partial_note = " This is a partial result." if partial else ""
        if style == "overview":
            return counts_text + partial_note
        if style == "recent_files":
            if re.search(r"\bprojects?\b", question):
                return counts_text + " Recent project activity needs a scan inside each project, which isn't supported yet."
            selected = [entry for entry in entries if entry["type"] == "file"]
            if not selected:
                return counts_text + " No files were returned." + partial_note
            if not all(_valid_modified_at(entry) for entry in selected):
                return counts_text + " Modification times are missing or invalid, so I can't reliably identify the most recently edited files." + partial_note
            selected = sorted(selected, key=lambda entry: entry["modified_at"], reverse=True)
            return counts_text + " The most recently modified files returned are " + _names(selected, 3) + "." + partial_note
        selected = entries
        if re.search(r"\b(files|filenames)\b", question) and not re.search(r"\b(directories|folders)\b", question):
            selected = [entry for entry in entries if entry["type"] == "file"]
        elif re.search(r"\b(directories|folders)\b", question) and not re.search(r"\bfiles\b", question):
            selected = [entry for entry in entries if entry["type"] == "directory"]
        if not selected:
            return counts_text + " No matching entries were returned."
        limit = len(selected)
        names = _names(selected, limit)
        # Exact requests still have a speech budget; explicitly point to the rest.
        if len(names) > 750:
            names = _names(selected, 5)
            limit = 5
        suffix = " The remaining entries are kept in the full result; see the detailed output on screen." if len(selected) > limit else ""
        return counts_text + " Entries include " + names + "." + suffix + partial_note

    payload = _payload(data)
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if isinstance(payload, list):
        if not payload:
            return "The tool returned no items."
        if len(payload) > 5:
            return f"The tool returned {len(payload)} items. The detailed results are shown on screen."
    # Text-only JSON arrays are common in third-party tools.
    if isinstance(text, str) and text.startswith("["):
        try:
            values = json.loads(text)
        except (ValueError, RecursionError):
            values = None
        if isinstance(values, list) and len(values) > 5:
            return f"The tool returned {len(values)} items. The detailed results are shown on screen."
    if len(text) > 600 or len(text.splitlines()) > 8:
        return "The tool returned a longer response. The detailed result is shown on screen."
    return None
