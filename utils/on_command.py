import os
import sys
import time
from importlib.machinery import SourceFileLoader


def run(message, commands):
    on_command(message, commands)


def on_command(msg, commands):
    # Make the message lowercase, split it into an array.
    msg = msg.lower().split()

    if not any(s in msg for s in commands):
        print("No command found in string")

    for i in range(1, len(msg)):
        if (msg[i] in commands):
            args = []  # This holds any params of the command
            cmd = msg[i]
            if (not commands[cmd].run(args)):
                clear_terminal()
                print("Command failed to execute... Please try again!")
        else:
            clear_terminal()
            # print("Command does not exist... Please try again!")
            print("Checking rest of messasge for command")
            time.sleep(1)
            clear_terminal()


def clear_terminal():
    return os.system('cls')
