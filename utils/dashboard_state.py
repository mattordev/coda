import json
import threading
import time
from pathlib import Path


_STATE_FILE = Path(__file__).resolve().parent.parent / "dashboard_state.json"
_STATE_LOCK = threading.Lock()
_MAX_MESSAGES = 12


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


def _clamp_messages(messages):
    if len(messages) <= _MAX_MESSAGES:
        return messages
    return messages[-_MAX_MESSAGES:]


def _normalize_text(text):
    if text is None:
        return ""
    return str(text).strip()


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


def _write_state_to_disk(state):
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _STATE_FILE.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as temp_file:
        json.dump(state, temp_file, indent=2)
    temp_path.replace(_STATE_FILE)


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


def _update_state(mutator):
    with _STATE_LOCK:
        state = _read_state_from_disk()
        mutator(state)
        state["updated_at"] = time.time()
        _write_state_to_disk(state)
        return state


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


def snapshot():
    with _STATE_LOCK:
        return _read_state_from_disk()


def touch_heartbeat(source="main"):
    heartbeat_source = _normalize_text(source) or "main"

    def mutator(state):
        state["heartbeat_at"] = time.time()
        state["heartbeat_source"] = heartbeat_source

    return _update_state(mutator)