"""Bounded filesystem follow-ups from host-proven observations, never tool instructions."""

from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from pathlib import Path, PureWindowsPath
import re
import time

from ai.tools.result_summary import _payload, _valid_modified_at

FOCUS_SECONDS = 120
MISSING = 'Which folder or file do you mean? Please list or search the folder again, or provide an absolute path.'


@dataclass(frozen=True)
class FollowUp:
    tool: str | None = None
    arguments: dict | None = None
    reply: str | None = None
    result: object = None


def focus_is_current(result, manager):
    """Validate focus before either explaining it or using it to select an action."""
    if result is None or not result.host_context:
        return False
    context = result.host_context
    token = manager.filesystem_focus_token(context['server'])
    return (token is not None and token == context['token']
            and 0 <= time.monotonic() - context['observed_at'] < FOCUS_SECONDS)


def _safe_child(root, name):
    if (not isinstance(name, str) or not name or name in ('.', '..')
            or any(char in name for char in '/\\:') or any(ord(char) < 32 for char in name)):
        return None
    return str(root / name)


def plan_follow_up(message, result, manager):
    """Plan at most one read/search, or present retained names with explicit scope."""
    text = message.strip().rstrip('.!?').casefold()
    # Full matches keep unrelated requests and embedded filenames out of control flow.
    names = re.fullmatch(r'(?:list|show|say|read)(?: me)? (?:all (?:the )?|the )?(?:file ?names|names|(?:most recently modified|most recent|latest|recent|newest) files)', text)
    filtering = re.fullmatch(r'(?:find|show|list)(?: me)? (?:all (?:the )?|the )?(markdown|text|python|\*\.[a-z0-9]+) files(?: (?:here|in that folder))?', text)
    searching = re.fullmatch(r'(?:search|look)(?: recursively)? (?:in )?(?:that|this) folder for (\*\.[a-z0-9]+|markdown|text|python)(?: files)?', text)
    reading = re.fullmatch(r'(?:read|open)(?: me)? (?:the )?(.{1,180})', text)
    continuation = text in ('continue reading', 'read more', 'read the next part', 'next part')
    if not (names or filtering or searching or continuation or reading):
        return None
    # A concrete path or new task should go through ordinary routing.
    if reading and (any(char in reading[1] for char in '/\\:') or reading[1] in ('all files', 'a file')):
        return None
    if result is None or not result.host_context:
        if result is not None and (names or filtering):
            return None
        # Only explicit contextual language claims missing focus; ordinary reads route normally.
        if names or filtering or searching or continuation or (reading and re.search(
                r'\b(that|this|one|first|second|third|newest|latest|recent)\b', reading[1])):
            return FollowUp(reply=MISSING)
        return None
    context = result.host_context
    if not focus_is_current(result, manager):
        return FollowUp(reply=MISSING)
    payload = _payload(result.data)
    if not isinstance(payload, dict):
        return FollowUp(reply=MISSING)
    arguments = context['arguments']
    path = arguments.get('path')
    if not isinstance(path, str) or not Path(path).is_absolute():
        return FollowUp(reply=MISSING)
    if continuation:
        offset = payload.get('next_offset_bytes')
        if context['tool'] != 'read_file' or payload.get('kind') != 'file_contents':
            return FollowUp(reply='Please read a file excerpt first.')
        if payload.get('has_more') is not True:
            return FollowUp(reply='That excerpt reached the end of the file.')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            return FollowUp(reply='The result has no valid continuation position. Please request a new excerpt.')
        return FollowUp('read_file', {'path': path, 'offset_bytes': offset,
                                     'max_bytes': arguments.get('max_bytes') or 4096})
    listing = context['tool'] == 'list_directory' and payload.get('kind') == 'directory_listing'
    search = context['tool'] == 'search_files' and payload.get('kind') == 'filesystem_search'
    if not (listing or search):
        return FollowUp(reply='Please list or search a folder before selecting a file.')
    root = Path(path)
    entries = payload.get('entries' if listing else 'matches')
    if not isinstance(entries, list) or len(entries) > 10000:
        return FollowUp(reply=MISSING)
    files = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('type') != 'file':
            continue
        candidate = _safe_child(root, entry.get('name')) if listing else entry.get('path')
        if not isinstance(candidate, str):
            continue
        target = Path(candidate)
        if not target.is_absolute() or '..' in target.parts or not target.is_relative_to(root):
            continue
        # Reject foreign drive syntax on POSIX too; paths are interpreted by the local host.
        if not listing and PureWindowsPath(candidate).drive and not PureWindowsPath(str(root)).drive:
            continue
        files.append(dict(entry, path=str(target)))
    patterns = {'markdown': '*.md', 'text': '*.txt', 'python': '*.py'}
    if searching:
        return FollowUp('search_files', {'path': path, 'pattern': patterns.get(searching[1], searching[1])})
    if names or filtering:
        chosen = files
        if filtering:
            pattern = patterns.get(filtering[1], filtering[1])
            chosen = [entry for entry in files if fnmatchcase(entry['name'].casefold(), pattern)]
        elif names and any(word in text for word in ('recent', 'newest', 'latest')) and all(_valid_modified_at(entry) for entry in files):
            chosen = sorted(files, key=lambda entry: entry['modified_at'], reverse=True)
        # Keep the filtered observation as focus; raw originals remain retained separately.
        partial = payload.get('partial') is True or payload.get('truncated') is True
        filtered = replace(result, data={'structuredContent': {
            'kind': 'directory_listing' if listing else 'filesystem_search',
            'entries' if listing else 'matches': chosen, 'partial': partial, 'truncated': partial,
        }})
        return FollowUp(reply=('From the retained immediate-folder listing: ' if listing else
                               'From the retained filename search: '), result=filtered)
    if not reading:
        return None
    selection = reading[1].strip('"\'')
    recent = selection in ('newest file', 'latest file', 'most recent file', 'most recently modified file',
                           'newest report', 'latest report', 'most recent report')
    if recent:
        if not files or not all(_valid_modified_at(entry) for entry in files):
            return FollowUp(reply="The returned files don't have enough modification-time information to choose the newest one.")
        if 'report' in selection:
            files = [entry for entry in files if 'report' in entry['name'].casefold()]
        newest = max((entry['modified_at'] for entry in files), default=None)
        chosen = [entry for entry in files if entry['modified_at'] == newest]
    elif selection in ('that', 'this', 'it', 'that one', 'this one', 'the file', 'file'):
        chosen = files
    elif selection in ('first file', 'first one', 'second file', 'second one', 'third file', 'third one'):
        index = {'first': 0, 'second': 1, 'third': 2}[selection.split()[0]]
        chosen = files[index:index + 1]
    else:
        chosen = [entry for entry in files if entry['name'].casefold() == selection]
        if not chosen and not re.search(r'\b(that|this|one|newest|latest|recent)\b', selection):
            return None
    if len(chosen) != 1:
        return FollowUp(reply='Which file do you mean? Please give its filename or an absolute path; the selection is missing or ambiguous.')
    if recent and (payload.get('partial') is True or payload.get('truncated') is True):
        return FollowUp(reply='That listing is partial, so I cannot identify the newest file in the folder. Please select a filename.')
    return FollowUp('read_file', {'path': chosen[0]['path']})
