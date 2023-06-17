# Imports
import os
import sys
import json
from importlib.machinery import SourceFileLoader
import utils.voice_recognizer as voice_recognizer

commands = {}  # ALL COMMANDS TO BE USED BY OPERATOR

def setup_commands():
    command_file_location = os.getcwd() + "\commands\\"
    sys.path.append(command_file_location)

    # Clear the existing commands dictionary
    commands.clear()

    for cmdFile in os.listdir(command_file_location):
        name = os.fsdecode(cmdFile)
        if name.endswith(".py"):
            command_path = os.path.join(command_file_location, cmdFile)
            commands[name.split(".py")[0].lower()] = command_path

    # Save the commands to a JSON file after the setup is complete
    save_commands()
            
def save_commands():
    print("----------------------------------------", flush=True)
    print("PRINTING FOUND COMMANDS:")
    print(commands)
    print("----------------------------------------")
    
    with open("commands.json", "w") as outfile:
        jsonCommands = json.dump(commands, outfile)
    
def save_wakewords(wakewords):
    jsonWakewords = json.dumps(wakewords)
    jsonWakewordsFile = open("wakewords.json", "w")
    jsonWakewordsFile.write(jsonWakewords)
    jsonWakewordsFile.close()

### MAIN ###
wakewords = ["coda", "kodak", "coder", "skoda", "powder"]

# Calls the voice recognizer to listen to the microphone
setup_commands()
save_wakewords(wakewords)
voice_recognizer.run(wakewords,commands, type='normal')
