# Imports
import os
import sys
from importlib.machinery import SourceFileLoader
import utils.voice_recognizer as voice_recognizer


commands = {}  # ALL COMMANDS TO BE USED BY OPERATOR

def setup_commands():
    command_file_location = os.getcwd() + "\commands\\"
    sys.path.append(command_file_location)

    
    for cmdFile in os.listdir(command_file_location):
        name = os.fsdecode(cmdFile)
        if(name.endswith(".py")):
            module = SourceFileLoader(
                cmdFile, command_file_location+cmdFile).load_module()
            commands[name.split(".py")[0].lower()] = module
            print(commands)

### MAIN ###
wakeword = {"coda", "kodak", "coder", "skoda", "powder"}

# Calls the voice recognizer to listen to the microphone
setup_commands()
voice_recognizer.run(wakeword, commands)
