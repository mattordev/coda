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

    
    for cmdFile in os.listdir(command_file_location):
        name = os.fsdecode(cmdFile)
        if(name.endswith(".py")):
            commands[name.split(".py")[0].lower()] = command_file_location+cmdFile
            module = SourceFileLoader(
                cmdFile, command_file_location+cmdFile).load_module()
            commands[name.split(".py")[0].lower()] = command_file_location+cmdFile
            
            # after setup we save the commands to a JSON file
            save_commands()
            
def save_commands():
    print(commands)
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
