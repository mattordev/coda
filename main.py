# Imports
import os
import sys
import json
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
    
import json
import requests
import semantic_version



def check_update_available(version_url):
    try:
        with open("version.json", "r") as json_file:
            json_data = json.load(json_file)
            saved_version = semantic_version.Version(json_data['version'])
    except (IOError, KeyError, json.JSONDecodeError):
        # Create new version.json with initial version number
        saved_version = semantic_version.Version('0.0.0')
        with open("version.json", "w") as json_file:
            json.dump({'version': str(saved_version)}, json_file)

    try:
        version_response = requests.get(version_url)
        if version_response.status_code == 200:
            latest_version = version_response.text.strip()
            print("New version has been dected! Please update!")
            return semantic_version.Version(latest_version) > saved_version
    except requests.RequestException:
        pass

    return False




### MAIN ###
wakewords = ["coda", "kodak", "coder", "skoda", "powder", "kodi", "system"]

# Calls the voice recognizer to listen to the microphone
setup_commands()
save_wakewords(wakewords)
check_update_available(version_url)
voice_recognizer.run(wakewords, commands, type='normal')
