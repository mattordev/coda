import json
import threading
import time
from pathlib import Path

## program flow example:
# User speaks -> record_user_message
# AI replies -> record_ai_response
# Main loop alive -> touch_heartbeat
# Browser polls /api/state -> snapshot output rendered in UI
##

# dash json, a threading lock and total messages to keep in json
_STATE_FILE = Path(__file__).resolve().parent.parent / "dashboard_state.json"
_STATE_LOCK = threading.Lock()
_MAX_MESSAGES = 12


# every read starts from this format, prevents corruption
def _default_state():
    return {
        "updated_at": 0.0,
        "heartbeat_at": 0.0,
        "heartbeat_source": "",
        "event_counter": 0,
        "ai_event_counter": 0,
        "last_user_message": "",
        "last_ai_message": "",
        "last_ai_motion": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0},
        "user_messages": [],
        "ai_messages": [],
    }

# enforces _MAX_MESSAGES limit by keeping only the most recent messages in the list
def _clamp_messages(messages):
    if len(messages) <= _MAX_MESSAGES:
        return messages
    return messages[-_MAX_MESSAGES:]

# normalizes text by stripping whitespace and converting to string, returns empty string for None - error catcher
def _normalize_text(text):
    if text is None:
        return ""
    return str(text).strip()

# normalizes tags by ensuring it's a list of unique, non-empty strings with spaces replaced by underscores, 
# returns empty list for None - error catcher
def _normalize_tags(tags):
    if tags is None:
        return []

    if not isinstance(tags, (list, tuple, set)):
        tags = [tags]

    normalized = []
    seen = set()
    for tag in tags:
        normalized_tag = _normalize_text(tag).lower().replace(" ", "_")
        if not normalized_tag or normalized_tag in seen:
            continue
        seen.add(normalized_tag)
        normalized.append(normalized_tag)

    return normalized

# defines a set of motions to cycle through for AI responses, uses an event counter - needs some more variety but good enough for now
def _motion_for_event(event_counter):
    motions = [
        {"x": 0, "y": -18, "scale": 1.05, "rotation": -2},
        {"x": 18, "y": -10, "scale": 1.08, "rotation": 3},
        {"x": -20, "y": 8, "scale": 1.06, "rotation": -4},
        {"x": 16, "y": 18, "scale": 1.1, "rotation": 2},
        {"x": -14, "y": -16, "scale": 1.07, "rotation": -3},
        {"x": 22, "y": 4, "scale": 1.04, "rotation": 4},
    ]
    return motions[event_counter % len(motions)]

# reads the dash state from disk, returns a safe state if error happens
def _read_state_from_disk():
    if not _STATE_FILE.exists():
        return _default_state()

    try:
        with _STATE_FILE.open("r", encoding="utf-8") as state_file:
            loaded_state = json.load(state_file)
    except (OSError, json.JSONDecodeError):
        return _default_state()

    state = _default_state()
    state.update({key: value for key, value in loaded_state.items() if key in state})
    state["user_messages"] = loaded_state.get("user_messages", []) or []
    state["ai_messages"] = loaded_state.get("ai_messages", []) or []
    state["last_ai_motion"] = loaded_state.get("last_ai_motion", state["last_ai_motion"])
    return state

# write the dash state to json, makes a temp file and then replaces the main file when safe to prevent corruption.
def _write_state_to_disk(state):
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _STATE_FILE.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as temp_file:
        json.dump(state, temp_file, indent=2)
    temp_path.replace(_STATE_FILE)

# shared helper to append a message to the state, used by both user messages and AI responses, 
# also clamps the message list to prevent it from growing indefinitely.
def _append_message(state, key, text, source):
    message = {
        "text": text,
        "source": source,
        "timestamp": time.time(),
        "tags": [],
    }
    state[key].append(message)
    state[key] = _clamp_messages(state[key])
    return message

# core transaction wrapper, returns the updated state after applying the mutator function, 
# ensures that all state updates are atomic and thread-safe by using a lock, also updates the "updated_at" timestamp on every change.
def _update_state(mutator):
    with _STATE_LOCK:
        state = _read_state_from_disk()
        mutator(state)
        state["updated_at"] = time.time()
        _write_state_to_disk(state)
        return state

# called from voice_recognition.py when a new user message is captured, normalizes the text and tags, 
# updates the state with the new message and increments the event counter, 
# if the text is empty after normalization it simply returns the current state without making changes
def record_user_message(text, source="voice", tags=None):
    message_text = _normalize_text(text)
    if not message_text:
        return _read_state_from_disk()

    message_tags = _normalize_tags(tags)

    def mutator(state):
        state["event_counter"] += 1
        state["last_user_message"] = message_text
        message = _append_message(state, "user_messages", message_text, source)
        message["tags"] = message_tags

    return _update_state(mutator)

# called before TTS in speak_response.py. Increments the event counter, updates the last AI message and motion, 
# and appends the message to the list of AI messages. If the text is empty after normalization, 
# it simply returns the current state without making any changes
def record_ai_response(text, source="assistant"):
    message_text = _normalize_text(text)
    if not message_text:
        return _read_state_from_disk()

    def mutator(state):
        state["event_counter"] += 1
        state["ai_event_counter"] += 1
        state["last_ai_message"] = message_text
        state["last_ai_motion"] = _motion_for_event(state["ai_event_counter"])
        _append_message(state, "ai_messages", message_text, source)

    return _update_state(mutator)

# lock protected read-only accessor. Returns latest full state from the json file.
def snapshot():
    with _STATE_LOCK:
        return _read_state_from_disk()

# called from the heartbeat loop in main.py - used for connection status pill
def touch_heartbeat(source="main"):
    heartbeat_source = _normalize_text(source) or "main"

    def mutator(state):
        state["heartbeat_at"] = time.time()
        state["heartbeat_source"] = heartbeat_source

    return _update_state(mutator)