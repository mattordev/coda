"""Deterministic summaries of returned data; no model calls or tool execution."""

import json
from math import isfinite
import re


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
                # Missing/invalid metadata must not masquerade as a known date.
                # sorted() leaves the original alphabetical display data intact.
                if entries and all(
                    isinstance(entry.get("modified_at"), (int, float))
                    and not isinstance(entry["modified_at"], bool)
                    and (isinstance(entry["modified_at"], int) or isfinite(entry["modified_at"]))
                    for entry in entries
                ):
                    entries = sorted(entries, key=lambda entry: entry["modified_at"], reverse=True)
                return [(entry["name"], "directory" if entry["type"] == "dir" else entry["type"])
                        for entry in entries], payload.get("truncated") is True
    # The speech renderer may limit content blocks; counts must use every
    # returned listing block, not just the excerpt prepared for reading.
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        text = "\n".join(block["text"] for block in data["content"]
                         if isinstance(block, dict) and block.get("type") == "text"
                         and isinstance(block.get("text"), str))
    lines = text.splitlines()
    if lines and all(re.fullmatch(r"\[(FILE|DIR)\] .+", line) for line in lines):
        return [(line.split(" ", 1)[1], "file" if line.startswith("[FILE]") else "directory")
                for line in lines], False
    return None


def _names(entries, limit):
    # Names remain quoted data. Bound each value independently before joining.
    return ", ".join('"' + name[:80].replace("\n", " ") + '"' for name, _ in entries[:limit])


def summarize_result(data, text, question):
    """Return grounded speech for known shapes, or None for natural short text."""
    directory = _directory_entries(data, text)
    question = question.casefold()
    if directory is not None:
        entries, partial = directory
        if not entries:
            return "No accessible entries were returned." if partial else "There are no accessible entries in that directory."
        # Answer an explicit filename question from the returned entries only.
        specific = [entry for entry in entries if re.search(
            r"(?<![\w.])" + re.escape(entry[0].casefold()) + r"(?![\w.])", question
        )] if re.search(r"\b(is|does|find|contain|has|have)\b", question) else []
        if specific:
            return "The listing includes " + _names(specific, 3) + "."
        files = sum(kind == "file" for _, kind in entries)
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
        selected = entries
        if re.search(r"\b(files|filenames)\b", question) and not re.search(r"\b(directories|folders)\b", question):
            selected = [entry for entry in entries if entry[1] == "file"]
        elif re.search(r"\b(directories|folders)\b", question) and not re.search(r"\bfiles\b", question):
            selected = [entry for entry in entries if entry[1] == "directory"]
        if not selected:
            return counts_text + " No matching entries were returned."
        explicit = bool(re.search(r"\b(all|every|exact)\b", question))
        limit = len(selected) if explicit else 3
        names = _names(selected, limit)
        # Exact requests still have a speech budget; explicitly point to the rest.
        if len(names) > 750:
            names = _names(selected, 5)
            limit = 5
        suffix = " The remaining entries are kept in the full result; see the detailed output on screen." if len(selected) > limit else ""
        partial_note = " This is a partial result." if partial else ""
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
