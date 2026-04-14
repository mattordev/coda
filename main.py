# Imports
import os
import sys
import json
import importlib
from importlib.machinery import SourceFileLoader
import utils.voice_recognizer as voice_recognizer
import utils.on_command as command
import utils.runtime_state as runtime_state
import utils.dashboard_state as dashboard_state
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
voice_stop_event = threading.Event()
voice_thread = None
heartbeat_stop_event = threading.Event()
heartbeat_thread = None

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
    return "-m" in sys.argv or "--manual" in sys.argv

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
if __name__ == "__main__":
    mic_name_arg = _get_flag_value("--mic")
    mic_index_arg = _get_flag_value("--mic-index")

    if mic_name_arg:
        os.environ["CODA_MIC_NAME"] = mic_name_arg

    if mic_index_arg:
        os.environ["CODA_MIC_INDEX"] = mic_index_arg

    if "--list-mics" in sys.argv:
        voice_recognizer.print_microphones()
        sys.exit(0)

    manual_assisstant_input = _is_manual_mode_requested()

# gets the commands directory in a portable way, checks both "Commands" and "commands" to account for different naming conventions
def _get_commands_dir():
    cwd = Path.cwd()
    if (cwd / "Commands").exists():
        return cwd / "Commands"
    return cwd / "commands"

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
            module = SourceFileLoader(name.split(
                ".py")[0].lower(), command_path).load_module()
            commands[name.split(".py")[0].lower()] = module

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
            except json.JSONDecodeError as e:
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
            # print(wakewords)
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

# calls voice recog run loop using wakewords, commands and a stop event.
def start_voice_recognition():
    voice_recognizer.run(wakewords, commands, mode='normal',
                         stop_event=voice_stop_event)

# Starts daemon thread for voice loop if not already alive.
def start_voice_thread():
    global voice_thread

    if voice_thread is not None and voice_thread.is_alive():
        return

    voice_stop_event.clear()
    voice_thread = threading.Thread(target=start_voice_recognition, daemon=True)
    voice_thread.start()

# signals the voice thread to stop and waits for it to finish.
def stop_voice_thread(timeout_seconds=4.0):
    voice_stop_event.set()

    if voice_thread is not None and voice_thread.is_alive():
        voice_thread.join(timeout=timeout_seconds)

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


### MAIN ###
startTimer = time.perf_counter()

wakewords = ["coda", "kodak", "coder", "skoda",
             "powder", "kodi", "system", "jeff"]

if check_update_available(version_url):
    # if there's an update available, re-find the commands
    setup_commands()
    print("Setting up commands as a new version was found...")
else:
    try:
        # load the commands from JSON.
        commands = load_commands()
        wakewords = load_wakewords()
        # print(commands)
    except FileNotFoundError:
        run_first_time_setup()

load_time = time.perf_counter()
print(f"C.O.D.A loaded in {round(load_time-startTimer, 2)} second(s)")

start_heartbeat_thread() # start heartbeat daemon for dashboard connection status

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
            stop_voice_thread()
            stop_heartbeat_thread()
            print("Exiting C.O.D.A")
            break

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

        # Debug output enabled in manual mode so command matching is visible.
        command.run(
            command_message,
            commands,
            debug=runtime_state.is_debug_enabled(default=True),
        )
    else:
        if keyboard_toggle_available:
            try:
                if keyboard.is_pressed('ctrl+b'):
                    manual_assisstant_input = True
                    stop_voice_thread()
                    print("VOICE RECOGNITION STOPPED. MANUAL MODE ENABLED")
                    print("Type commands directly. Type 'voice' to switch back or 'quit' to exit.")
                    time.sleep(0.3)
            except Exception as keyboard_error:
                keyboard_toggle_available = False
                print(f"Keyboard toggle unavailable: {keyboard_error}")
                print("Restart with -m or --manual to use manual mode.")

        time.sleep(0.05)
