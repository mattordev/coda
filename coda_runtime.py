from runtime.voice_worker import VoiceInputWorker
from runtime.runtime_queue import RuntimeQueues
from runtime.messages import ExecutionResult, InputSource, RuntimeRequest, SpeechTask, WorkerEvent, WorkerEventType, WorkerName
from runtime.follow_up import FollowUpState
from runtime.active_request import ActiveRequestState
from runtime.speech_playback import SpeechPlaybackController
from runtime.speech_worker import SpeechTaskProcessor, SpeechWorker
import utils.speak_response as speech

import os
import sys
import json
import importlib
from importlib.machinery import SourceFileLoader
import utils.voice_recognizer as voice_recognizer
import utils.on_command as command
import utils.runtime_state as runtime_state
import utils.dashboard_state as dashboard_state
from utils.console import configure_stdout_encoding
from colorama import Fore, init

import semantic_version
import requests
import keyboard
import time
import threading
import re
from pathlib import Path

commands = {}  # ALL COMMANDS TO BE USED BY OPERATOR
wakewords = []
version_url = 'https://raw.githubusercontent.com/mattordev/coda/main/version.json'
manual_assisstant_input = False
heartbeat_stop_event = threading.Event()
heartbeat_thread = None
voice_worker = None
runtime_queues = RuntimeQueues()
follow_up_state = FollowUpState()
execution_stop_event = threading.Event()
execution_thread = None
event_stop_event = threading.Event()
event_thread = None
active_request_state = ActiveRequestState()
speech_stop_event = threading.Event()
speech_playback_controller = SpeechPlaybackController()
speech_processor = SpeechTaskProcessor(
    speech.resolve_speech_providers,
    speech_playback_controller,
)
speech_worker = SpeechWorker(
    runtime_queues.speech,
    runtime_queues.events,
    speech_processor,
    speech_stop_event,
)
shutdown_lock = threading.Lock()
shutdown_started = threading.Event()
latest_request_lock = threading.Lock()
latest_request_id = None


# scans for cli args in the form of --flag value or --flag=value, returns the value or None if not found
def _get_flag_value(flag_name):
    for idx, arg in enumerate(sys.argv):
        if arg == flag_name and idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
        if arg.startswith(f"{flag_name}="):
            return arg.split("=", 1)[1]
    return None


# checks to see if manual mode should be enabled based on flag
def _is_manual_mode_requested():
    manual_mode_requested = "-m" in sys.argv or "--manual" in sys.argv

    if manual_mode_requested:
        runtime_state.set_input_mode("manual")

    return manual_mode_requested


# turns text into lowercase and tokenizes it, uses regex. Used for wakeword detection to filter punctuation and ensure consistent matching.
def _tokenize_text(message):
    return re.findall(r"[a-z0-9']+", message.lower())


# checks for wakewords in the message by comparing against the wakeword list, returns true if any wakeword is found. Uses set intersection for efficient matching.
def _has_wakeword(message, wakeword_list):
    message_words = set(_tokenize_text(message))
    wakeword_set = {w.lower() for w in wakeword_list}
    return not wakeword_set.isdisjoint(message_words)


# finds the earliest mention of the wakeword in the in the message, strips it and removes punctuation.
# eg. "Hey Coda, what's the weather?" -> "what's the weather?" If no wakeword is found, returns the original message.
def _strip_text_before_wakeword(message, wakeword_list):
    first_match = None

    for wakeword in wakeword_list:
        match = re.search(rf"\b{re.escape(wakeword.lower())}\b", message.lower())
        if match is None:
            continue

        if first_match is None or match.start() < first_match.start():
            first_match = match

    if first_match is None:
        return message.strip()

    return message[first_match.end():].strip(" ,.!?-")


# sets up microphone configuration based on cli args, also handles --list-mics which prints the available microphones and exits
def _apply_cli_microphone_flags():
    mic_name_arg = _get_flag_value("--mic")
    mic_index_arg = _get_flag_value("--mic-index")

    if mic_name_arg:
        os.environ["CODA_MIC_NAME"] = mic_name_arg

    if mic_index_arg:
        os.environ["CODA_MIC_INDEX"] = mic_index_arg

    if "--list-mics" in sys.argv:
        voice_recognizer.print_microphones()
        sys.exit(0)

    return _is_manual_mode_requested()


def _get_commands_dir():
    """Return the canonical command module directory."""
    return Path.cwd() / "commands"


# loads the command modules from the commands dir, fills the global commands dict and writes to json via save_commands.
def setup_commands():
    command_file_location = _get_commands_dir()
    sys.path.append(str(command_file_location))

    # Clear the existing commands dictionary
    commands.clear()

    for cmdFile in os.listdir(command_file_location):
        name = os.fsdecode(cmdFile)
        if name.endswith(".py") and not name.startswith("__"):
            command_path = os.path.join(str(command_file_location), cmdFile)
            module_name = name.split(".py")[0].lower()
            spec = importlib.util.spec_from_file_location(module_name, command_path)
            module = importlib.util.module_from_spec(spec) # this replaces the old depreciated load_module()

            if spec.loader is None:
                raise ImportError(f"Unable to load command module: {command_path}")

            spec.loader.exec_module(module)
            commands[module_name] = module

    # Save the commands to a JSON file after the setup is complete
    print("Saving commands...")
    save_commands()


# serializes the commands dict to a json file, names and file paths are saved for each command module.
# This allows for faster loading on subsequent runs by avoiding the need to scan the commands directory again.
# If the commands.json file is missing or corrupted, it will be re-created on the next run.
def save_commands():
    print("----------------------------------------", flush=True)
    print("PRINTING FOUND COMMANDS:")
    print(commands)
    print("----------------------------------------")

    serialized_commands = {}
    for cmd_name, cmd_module in commands.items():
        serialized_commands[cmd_name] = {
            'module': cmd_module.__file__,
            # Add any other relevant information from the module if needed
        }

    with open("commands.json", "w") as outfile:
        json.dump(serialized_commands, outfile)


# reads the commands json, validates it against actual command files in the command dir
# if the json is corrupted or stale it rebuilds it via rerunning setup_commands and then tries again.
def load_commands():
    commands = {}
    serialized_commands = {}  # Initialize to an empty dictionary

    try:
        # Add the command directory to module search path in a portable way.
        sys.path.append(str(_get_commands_dir()))

        with open("commands.json", "r") as infile:
            try:
                serialized_commands = json.load(infile)
            except json.JSONDecodeError:
                print(
                    "Error loading commands from JSON. Attempting to re-setup the commands...")
                setup_commands()
                # These commands won't load even though the commands.json file is present
                commands = load_commands()

        expected_command_names = {
            path.stem.lower()
            for path in _get_commands_dir().glob("*.py")
            if not path.name.startswith("__")
        }
        serialized_command_names = set(serialized_commands.keys())

        if expected_command_names != serialized_command_names:
            print("Command files changed. Rebuilding command cache...")
            setup_commands()
            return load_commands()

        for cmd_name, cmd_data in serialized_commands.items():
            module_path = cmd_data["module"]
            # Add any other relevant information from the JSON if needed

            # Get the module name from the file path
            module_name = Path(module_path).stem

            # Load the module using spec_from_file_location
            spec = importlib.util.spec_from_file_location(
                module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Add the command to the dictionary
            commands[cmd_name] = module

        print("Commands loaded successfully.")
    except FileNotFoundError as e:
        raise e

    return commands


# writes wakeword list to JSON.
def save_wakewords(wakewords):
    print("Saving wakewords...")
    jsonWakewords = json.dumps(wakewords)
    jsonWakewordsFile = open("wakewords.json", "w")
    jsonWakewordsFile.write(jsonWakewords)
    jsonWakewordsFile.close()
    print("Wakewords saved!")


# loads wakewords from json, converts to a list of strings via helper json_dict_to_string_array.
# If the file is missing, it raises a FileNotFoundError which is caught in main to trigger first time setup.
def load_wakewords():
    wakewords = []

    try:
        with open("wakewords.json", "r") as infile:
            # Store the loaded wakewords for string manipulation
            serialized_wakewords = json.load(infile)

            wakewords = json_dict_to_string_array(serialized_wakewords)
    except FileNotFoundError as e:
        raise e

    return wakewords


# compares local version.json with remote version on github.
# - if remote newer, prompts user for an update.
# - if user accepts, runs the update_manager which handles the update process and restarts the program.
# - returns true if an update was performed, false otherwise
def check_update_available(version_url):
    try:
        # Try to load the local version file
        try:
            with open("version.json", "r") as json_file:
                json_data = json.load(json_file)
                saved_version = semantic_version.Version(json_data['version'])
        except FileNotFoundError:
            # If the version file doesn't exist, create it with a default version
            saved_version = semantic_version.Version("0.0.0")
            with open("version.json", "w") as json_file:
                json.dump({"version": "0.0.0"}, json_file)

        # Fetch the latest version from the remote URL
        version_response = requests.get(version_url)
        if version_response.status_code == 200:
            response_json = version_response.json()
            latest_version = response_json['version']
            latest_semantic_version = semantic_version.Version(latest_version)

            if latest_semantic_version > saved_version:
                prompt = input(
                    "An update is available. Would you like to update? (y/n): ")
                if prompt.lower() == "y":
                    # Update the program using the update_manager
                    import utils.update_manager as update_manager
                    update_manager.main()
                    return True  # Updated successfully
                else:
                    init(autoreset=True)
                    print(
                        Fore.RED + "Update aborted. Continuing with current version..")
                    return False  # User chose not to update

    except (IOError, KeyError, requests.RequestException, ValueError) as e:
        print(f"Error checking for updates: {e}")

    return False

def submit_speech_response(text: str) -> bool:
    """Queue speech and associate it with the active request."""
    if not text.strip():
        return False

    request = active_request_state.active_request()

    if request is None:
        request = RuntimeRequest(
            message=text,
            source=InputSource.MANUAL,
        )

    if request.cancel_event.is_set():
        return False

    runtime_queues.speech.put(
        SpeechTask(
            request_id=request.request_id,
            text=text,
            cancel_event=request.cancel_event,
        )
    )
    return True

def submit_runtime_request(request: RuntimeRequest) -> str | None:
    global latest_request_id

    cancelled_request_id = None

    if request.replace_active:
        cancelled_request_id = active_request_state.cancel_active()

    with latest_request_lock:
        latest_request_id = request.request_id

    runtime_queues.requests.put(request)
    return cancelled_request_id


def _is_latest_request(request_id: str | None) -> bool:
    with latest_request_lock:
        return latest_request_id is None or latest_request_id == request_id

def cancel_active_request() -> str | None:
    """Request cancellation of the active runtime request."""
    request_id = active_request_state.cancel_active()
    speech_cancel_event = runtime_queues.speech.cancel_current()
    playback_cancelled = (
        speech_processor.stop(
            expected_cancel_event=speech_cancel_event,
        )
        if speech_cancel_event is not None
        else False
    )
    speech_cancelled = (
        speech_cancel_event is not None
        or playback_cancelled
    )

    if request_id is None and not speech_cancelled:
        print(
            "[RUNTIME] No active request to cancel",
            flush=True,
        )
        return None

    if runtime_state.is_debug_enabled(default=False):
        if request_id is not None:
            message = (
                "[RUNTIME] Cancellation requested for "
                f"request {request_id[:8]}"
            )
        else:
            message = "[RUNTIME] Speech cancellation requested"
    else:
        message = "[RUNTIME] Cancellation requested"
        
    print(message, flush=True)
    return request_id

def _create_execution_result(
    request: RuntimeRequest,
    command_result,
) -> ExecutionResult:
    """Convert a command result into a runtime execution result."""
    if request.cancel_event.is_set():
        return ExecutionResult(
            request_id=request.request_id,
            handled=False,
            cancelled=True,
        )

    return ExecutionResult(
        request_id=request.request_id,
        handled=command_result.handled,
        response_text=command_result.response_text,
        open_follow_up=(
            command_result.open_follow_up
            and request.source == InputSource.VOICE
        ),
    )
    
def _execution_loop(stop_event):
    """Process queued runtime requests untill system shutdown."""
    while not stop_event.is_set():
        request = runtime_queues.requests.get(timeout=0.1)
        
        if request is None:
            continue
        
        active_request_state.activate(request)
        
        request_label = (
            f"{request.source.value} request "
            f"{request.request_id[:8]}"
        )

        debug_enabled = runtime_state.is_debug_enabled(default=False)
        execution_started_at = time.perf_counter()
        
        queue_duration = max(
            0.0,
            time.time() - request.created_at,
        )
        
        if debug_enabled:
            processing_message = (
                f"[RUNTIME] Processing {request_label} "
                f"after {queue_duration:.2f}s queued"
            )
        else:
            processing_message = "[RUNTIME] Processing request"

        print(processing_message, flush=True)
        
        try:
            if request.cancel_event.is_set():
                execution_result = ExecutionResult(
                    request_id=request.request_id,
                    handled=False,
                    cancelled=True,
                )
            else:
                command_result = command.run(
                    request.message,
                    commands,
                    debug=debug_enabled,
                    cancel_event=request.cancel_event,
                )
                execution_result = _create_execution_result(
                    request,
                    command_result,
                )
                if (
                    execution_result.response_text is not None
                    and not execution_result.cancelled
                ):
                    dashboard_state.record_ai_response(
                        execution_result.response_text,
                        source="tts",
                    )
                    runtime_queues.speech.put(
                        SpeechTask(
                            request_id=request.request_id,
                            text=execution_result.response_text,
                            cancel_event=request.cancel_event,
                            open_follow_up=execution_result.open_follow_up,
                        )
                    )
        except Exception as error:
            execution_result = ExecutionResult(
                request_id=request.request_id,
                handled=False,
                error=str(error),
            )
        finally:
            active_request_state.clear(request.request_id)
            runtime_queues.requests.task_done()
            
        if execution_result.cancelled:
            outcome = "Cancelled"
        elif execution_result.error is not None:
            outcome = "Failed"
        elif not execution_result.handled:
            outcome = "Not handled"
        else:
            outcome = "Completed"

        execution_duration = time.perf_counter() - execution_started_at

        if debug_enabled:
            outcome_message = (
                f"[RUNTIME] {outcome} {request_label} "
                f"in {execution_duration:.2f}s"
            )
        else:
            outcome_message = f"[RUNTIME] Request {outcome.lower()}"

        print(outcome_message, flush=True)
                
        runtime_queues.events.put(execution_result)
        
def _event_loop(stop_event):
    """Process runtime results until shutdown"""
    while not stop_event.is_set():
        runtime_event = runtime_queues.events.get(timeout=0.1)

        if runtime_event is None:
            continue

        try:
            if isinstance(runtime_event, WorkerEvent):
                if runtime_event.worker != WorkerName.SPEECH:
                    continue

                if runtime_event.event_type == WorkerEventType.STARTED:
                    follow_up_state.close()
                    continue

                if (
                    runtime_event.event_type == WorkerEventType.COMPLETED
                    and runtime_event.open_follow_up
                ):
                    if _is_latest_request(runtime_event.request_id):
                        follow_up_state.open(
                            voice_recognizer.get_follow_up_timeout_seconds()
                        )
                else:
                    follow_up_state.close()

                if (
                    runtime_event.event_type == WorkerEventType.FAILED
                    and runtime_event.error is not None
                    and runtime_state.is_debug_enabled(default=False)
                ):
                    print(
                        "[RUNTIME] Speech failed for request "
                        f"{runtime_event.request_id}: "
                        f"{runtime_event.error}"
                    )

                continue

            if not isinstance(runtime_event, ExecutionResult):
                continue

            if (
                runtime_event.response_text is not None
                and not runtime_event.cancelled
                and runtime_event.error is None
            ):
                continue

            if (
                runtime_event.open_follow_up
                and not runtime_event.cancelled
                and runtime_event.error is None
            ):
                follow_up_state.open(
                    voice_recognizer.get_follow_up_timeout_seconds()
                )
            else:
                follow_up_state.close()

            if (
                runtime_event.error is not None
                and runtime_state.is_debug_enabled(default=False)
            ):
                print(
                    "[RUNTIME] Request "
                    f"{runtime_event.request_id} failed: "
                    f"{runtime_event.error}"
                )

        finally:
            runtime_queues.events.task_done()
            
def start_event_thread():
    """Start the runtime event-processing thread."""
    global event_thread
    
    if event_thread is not None and event_thread.is_alive():
        return
    
    event_stop_event.clear()
    event_thread = threading.Thread(
        target=_event_loop,
        args=(event_stop_event,),
        name="coda-events",
        daemon=True,
    )
    event_thread.start()
    
def stop_event_thread(timeout_seconds=4.0):
    """Stop the runtime event processing thread."""
    event_stop_event.set()

    if event_thread is not None and event_thread.is_alive():
        event_thread.join(timeout=timeout_seconds)
        
def start_execution_thread():
    """Start the runitme request-processing thread."""
    global execution_thread
    
    if execution_thread is not None and execution_thread.is_alive():
        return
    
    execution_stop_event.clear()
    execution_thread = threading.Thread(
        target=_execution_loop,
        args=(execution_stop_event,),
        name="coda-execution",
        daemon=True,
    )
    execution_thread.start()
    
def start_speech_thread():
    """Start the dedicated speech-playback worker."""
    speech_worker.start()


def stop_speech_thread(timeout_seconds=4.0):
    """Stop active speech and shut down the speech worker."""
    speech_worker.stop(timeout=timeout_seconds)

def stop_execution_thread(timeout_seconds=4.0):
    """Stop the runtime request-processing thread."""
    execution_stop_event.set()
    if execution_thread is not None and execution_thread.is_alive():
        execution_thread.join(timeout=timeout_seconds)

def start_voice_recognition(stop_event):
    """Run voice recog untill the worker receives a shutdown."""
    runtime_state.set_input_mode("wake word")
    voice_recognizer.run(
        wakewords, 
        commands, 
        mode='normal', 
        stop_event=stop_event,
        request_queue=runtime_queues.requests,
        submit_request=submit_runtime_request,
        follow_up_state=follow_up_state,
        cancel_active_request=cancel_active_request,
        is_speech_playing=speech_playback_controller.is_playing,
    )


def start_voice_thread():
    """Start the dedicated voice-input worker."""
    global voice_worker
    
    if voice_worker is None:
        voice_worker = VoiceInputWorker(
            start_voice_recognition,
            threading.Event(),
        )

    voice_worker.start()


def stop_voice_thread(timeout_seconds=4.0):
    """Stop the dedicated voice-input worker."""
    if voice_worker is not None:
        voice_worker.stop(timeout=timeout_seconds)


# calls dashboard_state with a delay, this is used to feed the connection pill on the dashboard.
def _heartbeat_loop(interval_seconds=1.5):
    while not heartbeat_stop_event.is_set():
        try:
            dashboard_state.touch_heartbeat(source="main")
        except Exception as heartbeat_error:
            print(f"Heartbeat update failed: {heartbeat_error}")

        heartbeat_stop_event.wait(interval_seconds)


# starts the heartbeat daemon if not already alive.
def start_heartbeat_thread():
    global heartbeat_thread

    if heartbeat_thread is not None and heartbeat_thread.is_alive():
        return

    heartbeat_stop_event.clear()
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()


# stops and joins the heartbeat thread
def stop_heartbeat_thread(timeout_seconds=2.0):
    heartbeat_stop_event.set()

    if heartbeat_thread is not None and heartbeat_thread.is_alive():
        heartbeat_thread.join(timeout=timeout_seconds)


def shutdown_runtime(timeout_seconds=4.0):
    """Cancel active work and stop every runtime worker exactly once."""
    with shutdown_lock:
        if shutdown_started.is_set():
            return False
        shutdown_started.set()

    follow_up_state.close()
    stop_voice_thread(timeout_seconds=timeout_seconds)

    active_request_state.cancel_active()
    stop_execution_thread(timeout_seconds=timeout_seconds)

    speech.configure_speech_submitter(None)
    stop_speech_thread(timeout_seconds=timeout_seconds)
    stop_event_thread(timeout_seconds=timeout_seconds)
    stop_heartbeat_thread(timeout_seconds=timeout_seconds)
    return True


# runs first time setup if command/wakeword files are missing. can be called whenever safely as resets program flow.
def run_first_time_setup():
    print("Commands file not found. Assuming first time setup...")
    setup_commands()
    save_wakewords(wakewords)


### UTIL FUNCTIONS ##
# These should probably be moved to a seperate .py file


# filters a json-loaded dict down to string entries
def json_dict_to_string_array(jsonData):
    string_array = []
    for item in jsonData:
        # Assuming the strings are at the top level of the json structure
        if isinstance(item, str):
            string_array.append(item)
    return string_array


#####################


def _run_runtime():
    global commands
    global wakewords
    global manual_assisstant_input

    startTimer = time.perf_counter()

    manual_assisstant_input = _apply_cli_microphone_flags()

    wakewords = [
        "coda", "kodak", "coder", "skoda",
        "powder", "kodi", "system", "jeff"
        ]

    if check_update_available(version_url):
        # if there's an update available, re-find the commands
        setup_commands()
        print("Setting up commands as a new version was found...")
    else:
        try:
            # load the commands from JSON.
            commands = load_commands()
            wakewords = load_wakewords()
        except FileNotFoundError:
            run_first_time_setup()

    command.configure_intent_router(commands)

    load_time = time.perf_counter()
    print(f"C.O.D.A loaded in {round(load_time-startTimer, 2)} second(s)")

    start_heartbeat_thread()  # start heartbeat daemon for dashboard connection status
    
    start_execution_thread() # start the execution worker
    
    start_event_thread() # start the event thread

    start_speech_thread() # start the speech thread

    speech.configure_speech_submitter(submit_speech_response)

    if manual_assisstant_input:
        print("MANUAL MODE ENABLED")
        print("Type commands directly. Type 'voice' to switch to voice mode or 'quit' to exit.")
    else:
        print("VOICE MODE ENABLED")
        print("Press Ctrl+B to switch to manual mode, or restart with -m / --manual.")
        start_voice_thread()

    keyboard_toggle_available = True

    # Main loop
    while True:
        if manual_assisstant_input:
            manual_message = input("manual> ").strip()

            if not manual_message:
                continue

            normalized_manual_message = manual_message.lower()

            if normalized_manual_message in ("quit", "exit"):
                return

            if normalized_manual_message in ("voice", "/voice"):
                manual_assisstant_input = False
                print("VOICE MODE ENABLED")
                start_voice_thread()
                continue

            if not _has_wakeword(normalized_manual_message, wakewords):
                print("[MANUAL] Wakeword not detected. Prefix your request with a wakeword.")
                continue

            command_message = _strip_text_before_wakeword(
                normalized_manual_message,
                wakewords,
            )

            if not command_message:
                print("[MANUAL] Wakeword detected without follow-up text.")
                continue

            submit_runtime_request(
                RuntimeRequest(
                    message=command_message,
                    source=InputSource.MANUAL,
                )
            )
        else:
            if keyboard_toggle_available:
                try:
                    if keyboard.is_pressed('ctrl+b'):
                        manual_assisstant_input = True
                        runtime_state.set_input_mode("manual")
                        stop_voice_thread()
                        print("VOICE RECOGNITION STOPPED. MANUAL MODE ENABLED")
                        print("Type commands directly. Type 'voice' to switch back or 'quit' to exit.")
                        time.sleep(0.3)
                except Exception as keyboard_error:
                    keyboard_toggle_available = False
                    print(f"Keyboard toggle unavailable: {keyboard_error}")
                    print("Restart with -m or --manual to use manual mode.")

            time.sleep(0.05)


def main():
    """Run CODA and guarantee runtime cleanup on every exit path."""
    configure_stdout_encoding()
    shutdown_started.clear()

    try:
        _run_runtime()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_runtime()

    print("Exiting C.O.D.A")


if __name__ == "__main__":
    main()
