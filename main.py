# Imports
import os
import sys
import json
import importlib
from importlib.machinery import SourceFileLoader
import utils.voice_recognizer as voice_recognizer

import semantic_version
import requests

commands = {}  # ALL COMMANDS TO BE USED BY OPERATOR
version_url = 'https://raw.githubusercontent.com/mattordev/coda/main/version.json'


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
    save_commands()


def save_commands():
    print("----------------------------------------", flush=True)
    print("PRINTING FOUND COMMANDS:")
    print(commands)
    print("----------------------------------------")

    serialized_commands = {}
    for cmd_name, cmd_module in commands.items():
        serialized_commands[cmd_name] = {
            'module': cmd_module.__name__,
            # Add any other relevant information from the module if needed
        }

    with open("commands.json", "w") as outfile:
        json.dump(serialized_commands, outfile)


def save_wakewords(wakewords):
    jsonWakewords = json.dumps(wakewords)
    jsonWakewordsFile = open("wakewords.json", "w")
    jsonWakewordsFile.write(jsonWakewords)
    jsonWakewordsFile.close()


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


### MAIN ###
wakewords = ["coda", "kodak", "coder", "skoda", "powder", "kodi", "system"]

# if check_update_available(version_url):
#     # if there's an update available, re-find the commands
#     setup_commands()
#     print("Setting up commands as a new version was found...")
# else:
#     try:
#         # load the commands from JSON.
#         with open("commands.json", "r") as command_file:
#             print("Loading commands from JSON")

#             print(commands)
#     except FileNotFoundError:
#         run_first_time_setup()

run_first_time_setup()
save_wakewords(wakewords)
# Calls the voice recognizer to listen to the microphone
voice_recognizer.run(wakewords, commands, type='normal')
