# TODO: Implement PocketSphinx for offline voice recognition and use google's API for when there is a connection

import os
import re
import http.client
import speech_recognition as sr
# import pocketsphinx5 as ps5
import utils.dashboard_state as dashboard_state
import utils.on_command as command
import utils.runtime_state as runtime_state
import utils.stt_service as stt_service
from runtime.messages import InputSource, RuntimeRequest
from runtime.runtime_queue import RuntimeQueue
from runtime.follow_up import FollowUpState

# Wakeword is our list of trigger words, commands is the commands list and type defines whether the voicerecognition is in response to a question.
# Currently not using `wakewords.json` or `commands.json` but will be in the future

def _get_float_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def _get_bool_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _get_listen_timeout_seconds():
    return _get_float_env("CODA_LISTEN_TIMEOUT", 3.0)


def _get_phrase_time_limit_seconds():
    return _get_float_env("CODA_PHRASE_TIME_LIMIT", 12.0)


def _get_pause_threshold_seconds():
    return _get_float_env("CODA_PAUSE_THRESHOLD", 1.2)


def _get_calibration_seconds():
    return _get_float_env("CODA_CALIBRATION_SECONDS", 0.8)


def get_follow_up_timeout_seconds():
    return _get_float_env("CODA_FOLLOWUP_TIMEOUT", 10.0)


def _voice_command_debug_enabled():
    return runtime_state.is_debug_enabled(default=False)


def _list_mics_on_start():
    return _get_bool_env("CODA_LIST_MICS_ON_START", False)


def _tokenize_text(message):
    return re.findall(r"[a-z0-9']+", message.lower())


def _has_wakeword(message, wakewords):
    message_words = set(_tokenize_text(message))
    wakeword_set = {w.lower() for w in wakewords}
    return not wakeword_set.isdisjoint(message_words)


def _strip_text_before_wakeword(message, wakewords):
    first_match = None

    for wakeword in wakewords:
        match = re.search(rf"\b{re.escape(wakeword.lower())}\b", message.lower())
        if match is None:
            continue

        if first_match is None or match.start() < first_match.start():
            first_match = match

    if first_match is None:
        return message.strip()

    return message[first_match.end():].strip(" ,.!?-")


def _normalize_message(message):
    return message.strip().lower().strip(" ,.!?-")

def _queue_runtime_request(
    message: str,
    request_queue: RuntimeQueue[RuntimeRequest],
) -> RuntimeRequest:
    """Create and queue a voice runtime request"""
    request = RuntimeRequest(
        message=message,
        source=InputSource.VOICE,
    )
    request_queue.put(request)
    return request


def _is_follow_up_stop_phrase(message):
    normalized_message = _normalize_message(message)
    return normalized_message in {
        "stop",
        "cancel",
        "never mind",
        "nevermind",
        "that's all",
        "that is all",
        "done",
        "forget it",
    }


def _get_microphone():
    names = sr.Microphone.list_microphone_names()
    mic_index = os.getenv("CODA_MIC_INDEX", "").strip()
    mic_name = os.getenv("CODA_MIC_NAME", "").strip().lower()

    if _list_mics_on_start():
        print_microphones()

    if mic_index:
        try:
            mic_index = int(mic_index)
            if 0 <= mic_index < len(names):
                print(f"Using microphone index {mic_index}: {names[mic_index]}")
                return sr.Microphone(device_index=mic_index)
            print(
                f"CODA_MIC_INDEX '{mic_index}' out of range. Using system default microphone.")
            return sr.Microphone()
        except ValueError:
            print(f"Invalid CODA_MIC_INDEX '{mic_index}'. Using system default microphone.")
            return sr.Microphone()

    if mic_name:
        matches = [(i, name) for i, name in enumerate(
            names) if mic_name in name.lower()]
        if matches:
            match_index, match_name = matches[0]
            if len(matches) > 1:
                print(
                    f"Multiple mics matched '{mic_name}'. Using first match: [{match_index}] {match_name}")
            else:
                print(f"Using microphone by name match: [{match_index}] {match_name}")
            return sr.Microphone(device_index=match_index)

        print(
            f"No microphone matched CODA_MIC_NAME '{mic_name}'. Using system default microphone.")

    print("Using system default microphone.")
    return sr.Microphone()


def print_microphones():
    names = sr.Microphone.list_microphone_names()
    print("Available microphones:")
    for i, name in enumerate(names):
        print(f"[{i}] {name}")


def run(
    wakeword, 
    commands, mode=None,
    stop_event=None,
    request_queue: RuntimeQueue[RuntimeRequest] | None = None,
    follow_up_state: FollowUpState | None = None,
    **kwargs):
    if kwargs:
        unexpected = ", ".join(sorted(kwargs.keys()))
        raise TypeError(f"run() got unexpected keyword argument(s): {unexpected}")
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = _get_pause_threshold_seconds()
    active_follow_up_state = follow_up_state or FollowUpState()

    try:
        microphone = _get_microphone()
    except OSError as e:
        print(f"Microphone setup failed: {e}")
        return

    try:
        with microphone as source:
            print("Calibrating microphone for ambient noise...")
            recognizer.adjust_for_ambient_noise(
                source, duration=_get_calibration_seconds())
            print(
                f"Microphone ready (energy threshold: {int(recognizer.energy_threshold)})")
    except OSError as e:
        print(f"Microphone calibration failed: {e}")
        return

    while True:
        if stop_event is not None and stop_event.is_set():
            return

        debug_enabled = runtime_state.is_debug_enabled(
            default=_voice_command_debug_enabled()
        )

        follow_up_active = active_follow_up_state.is_active()
        
        current_mode = mode or "normal"

        if current_mode == "normal" and follow_up_active:
            print('Waiting for follow-up', flush=True)
        elif current_mode == "normal":
            print('Ready to accept commands', flush=True)
        elif current_mode == 'response':
            print('Waiting for response', flush=True)

        try:
            recognizer.pause_threshold = _get_pause_threshold_seconds()
            with microphone as source:
                audio = recognizer.listen(
                    source,
                    timeout=_get_listen_timeout_seconds(),
                    phrase_time_limit=_get_phrase_time_limit_seconds(),
                )

            if stop_event is not None and stop_event.is_set():
                return

            speech, provider_used = stt_service.transcribe_audio(recognizer, audio)
            if not speech:
                continue

            speech_text = speech.strip()
            message = speech.lower()
            follow_up_state = active_follow_up_state.is_active()
            has_wakeword = _has_wakeword(message, wakeword)
            wakeword_command_message = _strip_text_before_wakeword(
                message,
                wakeword,
            ) if has_wakeword else ""

            event_tags = []
            if follow_up_active:
                event_tags.append("follow_up")
                if _is_follow_up_stop_phrase(message):
                    event_tags.append("follow_up_stop")
            elif has_wakeword:
                event_tags.append("wakeword_detected")
                if not wakeword_command_message:
                    event_tags.append("wakeword_only")
            else:
                event_tags.append("unrelated_speech")

            dashboard_state.record_user_message(
                speech_text,
                source=provider_used,
                tags=event_tags,
            )

            display_message(f"Heard: {message}")
            if debug_enabled:
                print(f"[VOICE] Speech provider used: {provider_used}")

            if follow_up_active:
                if _is_follow_up_stop_phrase(message):
                    active_follow_up_state.close()
                    if debug_enabled:
                        print("[VOICE] Follow-up ended by user.")
                    continue

                command_message = message.strip()
                if debug_enabled:
                    print("[VOICE] Follow-up mode active. Processing without wakeword.")
            elif has_wakeword:
                command_message = wakeword_command_message

                if not command_message:
                    if debug_enabled:
                        print("[VOICE] Wakeword detected without follow-up speech.")
                    continue

                if debug_enabled:
                    print(f"[VOICE] Wakeword detected. Parsed message: {command_message}")
            else:
                print("[VOICE] Wakeword not detected. Ignoring phrase.")
                active_follow_up_state.close()
                continue

            if request_queue is not None:
                _queue_runtime_request(command_message, request_queue)
                active_follow_up_state.close()
                continue    

            result = command.run(command_message, commands, debug=debug_enabled)

            if result.open_follow_up:
                follow_up_timeout_seconds = get_follow_up_timeout_seconds()
                active_follow_up_state.open(follow_up_timeout_seconds)
                if debug_enabled:
                    print(
                        "[VOICE] Follow-up window opened for "
                        f"{round(follow_up_timeout_seconds, 1)} seconds."
                    )
            elif follow_up_active:
                active_follow_up_state.close()
                if debug_enabled:
                    print("[VOICE] Follow-up window closed.")

        except sr.UnknownValueError:
            if stop_event is not None and stop_event.is_set():
                return
            print("Heard audio but could not understand speech.")
            continue
        except sr.RequestError as e:
            if stop_event is not None and stop_event.is_set():
                return
            print(f"Speech recognition request failed: {e}")
            continue
        except (http.client.IncompleteRead, ConnectionError, BrokenPipeError) as e:
            if stop_event is not None and stop_event.is_set():
                return
            print(f"Network connection error during speech recognition: {e}")
            continue
        except sr.WaitTimeoutError:
            if stop_event is not None and stop_event.is_set():
                return
            continue
        except stt_service.SpeechToTextError as e:
            if stop_event is not None and stop_event.is_set():
                return
            print(f"Speech recognition failed: {e}")
            continue
        except OSError as e:
            print(f"Microphone runtime error: {e}")
            return


def display_message(message):
    print(message, flush=True)


def clear_terminal():
    return os.system('cls')

