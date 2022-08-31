# Imports
import os
import sys
from importlib.machinery import SourceFileLoader
import speech_recognition as sr

import utils.voice_recognizer as voice_recognizer


def clear_terminal():
    return
    return os.system('cls')


def setup_commands():
    command_file_location = os.getcwd() + "\commands\\"
    sys.path.append(command_file_location)

    commands = {}  # ALL COMMANDS TO BE USED BY OPERATOR
    for cmdFile in os.listdir(command_file_location):
        name = os.fsdecode(cmdFile)
        if(name.endswith(".py")):
            module = SourceFileLoader(
                cmdFile, command_file_location+cmdFile).load_module()
            commands[name.split(".py")[0].lower()] = module


def on_command(msg):
    msg = msg.lower().split()
    if not any(s in msg for s in commands):
        print("No command found in string")

    # Make the message lowercase, split it into an array.

    for i in range(1, len(msg)):
        if (msg[i] in commands):
            args = []  # This holds any params of the command
            cmd = msg[i]
            if (not commands[cmd].run(args)):
                clear_terminal()
                print("Command failed to execute... Please try again!")
        else:
            # clear_terminal()
            #print("Command does not exist... Please try again!")
            print("Checking rest of messasge for command")
            clear_terminal()


### MAIN ###
wakeword = {"coda", "kodak", "coder", "skoda"}

# Calls the voice recognizer to listen to the microphone
voice_recognizer.run(wakeword)
