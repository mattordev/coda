"""Deterministic summaries of returned data; no model calls or tool execution."""

import json
from datetime import datetime, timezone
from pathlib import PureWindowsPath
from math import isfinite
import re

from ai.privacy.sanitizer import sanitize_text


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
    # Keep semantic filenames exact; pronunciation belongs to the TTS copy.
    names = []
    for entry in entries[:limit]:
        original = sanitize_text(entry["name"])
        name = original[:80].replace("\n", " ")
        name = re.sub(r"\s+", " ", name).strip()
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


def _filename_search(data):
    """Recognise filename observations without treating them as directory facts."""
    payload = _payload(data)
    if isinstance(payload, dict) and payload.get("kind") == "filesystem_search":
        matches = payload.get("matches")
        if isinstance(matches, list) and all(
            isinstance(match, dict) and isinstance(match.get("name"), str)
            and match.get("type") == "file" for match in matches
        ):
            return matches, payload.get("partial") is True or payload.get("truncated") is True
    return None


def filesystem_fact_summary(data, text, question):
    """Speak confirmed filesystem facts without a second model request."""
    payload = _payload(data)
    if not isinstance(payload, dict):
        return None
    kind = payload.get('kind')
    if kind == 'file_contents' and payload.get('byte_length') == 0:
        return 'The file contains no readable text.'
    if kind == 'filesystem_content_search':
        matches = payload.get('matches')
        if not isinstance(matches, list) or not all(
                isinstance(match, dict) and isinstance(match.get('name'), str)
                and type(match.get('line')) is int and match['line'] > 0 for match in matches):
            return None
        count = len(matches)
        answer = (f'The content search returned {count} matching ' + ('line.' if count == 1 else 'lines.')) if count else 'No matching text was returned.'
        if payload.get('partial') is True or payload.get('truncated') is True:
            answer += ' This is an incomplete content search.'
        if matches:
            first = matches[0]
            answer += ' The first match is in ' + _names([first], 1) + f' at line {first["line"]}.'
            answer += ' Say show tool output for the matching paths and excerpts.'
        return answer
    name = PureWindowsPath(str(payload.get('destination' if kind == 'filesystem_copied' else 'path', ''))).name
    label = _names([{'name': name}], 1) if name else 'The item'
    if kind == 'filesystem_copied' and name and payload.get('type') in ('file', 'directory'):
        return ('Copied the folder to ' if payload['type'] == 'directory' else 'Copied the file to ') + label + '.'
    if kind == 'filesystem_changed' and name and payload.get('operation') in ('edit', 'append'):
        answer = ('Updated the file ' if payload['operation'] == 'edit' else 'Appended to the file ') + label + '.'
        if payload.get('recovery_paths'):
            answer += ' Recovery files still need cleanup; say show tool output for their locations.'
        return answer
    if kind == 'filesystem_created' and payload.get('type') in ('file', 'directory') and name:
        item = 'folder' if payload['type'] == 'directory' else 'file'
        return f'Created the {item} {label}.'
    if kind == 'file_metadata' and payload.get('type') in ('file', 'directory', 'other') and name:
        item = {'directory': 'folder', 'file': 'file', 'other': 'filesystem item'}[payload['type']]
        answer = f'{label} is a {item}.'
        size = payload.get('size_bytes')
        if item == 'file' and isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            answer += f' Its size is {size:,} ' + ('byte.' if size == 1 else 'bytes.')
        if _valid_modified_at(payload):
            try:
                date = datetime.fromtimestamp(payload['modified_at'], timezone.utc)
                answer += f' It was last modified on {date:%d %B %Y at %H:%M} UTC.'
            except (ValueError, OSError, OverflowError):
                pass
        return answer
    if kind == 'file_contents' and payload.get('partial') is True:
        excerpt = re.sub(r'^\[Partial file content:[^\n]*\]\s*\n?', '', text)
        intro = 'Here is part of the file.'
        if payload.get('has_more') is True:
            intro += ' There is more to read; say read the next part.'
        else:
            intro += ' This excerpt reaches the end of the file.'
        if len(excerpt) > 750 or len(excerpt.splitlines()) > 8:
            return intro + ' Say show tool output for the excerpt, or ask me to summarize it.'
        return intro + ('\n' + excerpt if excerpt else '')
    return None


def summarize_result(data, text, question):
    """Return grounded speech for known shapes, or None for natural short text."""
    facts = filesystem_fact_summary(data, text, question)
    if facts is not None:
        return facts
    search = _filename_search(data)
    if search is not None:
        matches, partial = search
        count = len(matches)
        answer = (f"The filename search returned {count} matching " + ("file." if count == 1 else "files.")) if count else "No matching filenames were returned."
        if partial:
            answer += " This is a partial search."
        if matches:
            style = _presentation_style(_question_text(question))
            selected = matches
            if style == 'recent_files':
                if not all(_valid_modified_at(entry) for entry in matches):
                    return answer + " Modification times are missing or invalid, so I can't reliably identify the most recently edited files."
                selected = sorted(matches, key=lambda entry: entry['modified_at'], reverse=True)
            limit = count if style == 'all_names' else 3
            if len(_names(selected, limit)) > 750:
                limit = 5
            answer += (" The most recently modified matches returned are " if style == 'recent_files' else " Matches include ") + _names(selected, limit) + "."
            if count > limit:
                answer += " Say show tool output for the other paths."
        return answer
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
            return "The listing includes " + _names(specific, 3) + "." + (" This is a partial result." if partial else "")
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
        suffix = " The remaining entries are kept in the full result; say show tool output for details." if len(selected) > limit else ""
        return counts_text + " Entries include " + names + "." + suffix + partial_note

    payload = _payload(data)
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if isinstance(payload, list):
        if not payload:
            return "The tool returned no items."
        if len(payload) > 5:
            return f"The tool returned {len(payload)} items. Say show tool output for details."
    # Text-only JSON arrays are common in third-party tools.
    if isinstance(text, str) and text.startswith("["):
        try:
            values = json.loads(text)
        except (ValueError, RecursionError):
            values = None
        if isinstance(values, list) and len(values) > 5:
            return f"The tool returned {len(values)} items. Say show tool output for details."
    if len(text) > 600 or len(text.splitlines()) > 8:
        return "The tool returned a longer response. Say show tool output for the details."
    return None
