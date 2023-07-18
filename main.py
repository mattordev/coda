# Imports
import os
import sys
import json
import importlib
from importlib.machinery import SourceFileLoader
import utils.voice_recognizer as voice_recognizer

import semantic_version
import requests
from pynput import keyboard

commands = {}  # ALL COMMANDS TO BE USED BY OPERATOR
wakewords = []
version_url = 'https://raw.githubusercontent.com/mattordev/coda/main/version.json'
manual_assisstant_input = True


def setup_commands():
    command_file_location = os.getcwd() + "\commands\\"
    sys.path.append(command_file_location)

    # Clear the existing commands dictionary
    commands.clear()

    for cmdFile in os.listdir(command_file_location):
        name = os.fsdecode(cmdFile)
        if name.endswith(".py"):
            command_path = os.path.join(command_file_location, cmdFile)
            module = SourceFileLoader(name.split(
                ".py")[0].lower(), command_path).load_module()
            commands[name.split(".py")[0].lower()] = module

    # Save the commands to a JSON file after the setup is complete
    print("Saving commands...")
    save_commands()


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


def load_commands():
    commands = {}
    try:
        # Add the directory to the module search path, without this, the commands won't load - need to make this universal across different machines.
        sys.path.append('G:\\GitRepos\\coda\\commands')

        with open("commands.json", "r") as infile:
            serialized_commands = json.load(infile)

        for cmd_name, cmd_data in serialized_commands.items():
            module_path = cmd_data["module"]
            # Add any other relevant information from the JSON if needed

            # Get the module name from the file path
            module_name = module_path.split("\\")[-1].split(".")[0]

            # Load the module using spec_from_file_location
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Add the command to the dictionary
            commands[cmd_name] = module

        print("Commands loaded successfully.")
    except FileNotFoundError as e:
        raise e

    return commands


def save_wakewords(wakewords):
    print("Saving wakewords...")
    jsonWakewords = json.dumps(wakewords)
    jsonWakewordsFile = open("wakewords.json", "w")
    jsonWakewordsFile.write(jsonWakewords)
    jsonWakewordsFile.close()
    print("Wakewords saved!")
    
def load_wakewords():
    wakewords = ()
    
    try:
        with open("wakewords.json", "r") as infile:
            # Store the loaded wakewords for string manipulation
            serialized_wakewords = json.load(infile)
            
            wakewords = json_dict_to_string_array(serialized_wakewords)
            # print(wakewords)
    except FileNotFoundError as e:
        raise e
    
    return wakewords


def check_update_available(version_url):
    try:
        with open("version.json", "r") as json_file:
            json_data = json.load(json_file)
            saved_version = semantic_version.Version(json_data['version'])

        version_response = requests.get(version_url)
        if version_response.status_code == 200:
            response_json = version_response.json()
            latest_version = response_json['version']
            latest_semantic_version = semantic_version.Version(latest_version)
            return latest_semantic_version > saved_version  # Compare the versions

    except (IOError, KeyError, requests.RequestException, ValueError):
        pass

    return False


def run_first_time_setup():
    print("Commands file not found. Assuming first time setup...")
    setup_commands()
    save_wakewords(wakewords)
    
    
### UTIL FUNCTIONS ##

# These should probably be moved to a seperate .py file
def json_dict_to_string_array(jsonData):
    string_array = []
    for item in jsonData:
        # Assuming the strings are at the top level of the json structure
        if isinstance(item, str):
            string_array.append(item)
    return string_array
    
def handle_keypress(key):
    global manual_assistant_input

    # Check for the correct key combination to toggle manual mode
    if key == keyboard.Key.shift and key.char == 'm':
        # Toggle the boolean flag
        manual_assistant_input = not manual_assistant_input
        print(f"Manual input mode {'enabled' if manual_assistant_input else 'disabled'}")

#####################


### MAIN ###
wakewords = ["coda", "kodak", "coder", "skoda", "powder", "kodi", "system"]

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

# # Create a listener for key events
# listener = keyboard.Listener(on_press=handle_keypress)
# listener.start()

# # Keep the main thread running
# listener.join()

# Calls the voice recognizer to listen to the microphone
voice_recognizer.run(wakewords, commands, type='normal')
