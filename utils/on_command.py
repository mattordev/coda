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

    # Find the intersection between the message words and the command words
    matched_commands = set(msg) & set(commands.keys())

    if len(matched_commands) > 0:
        # If there are matched commands, iterate over them
        for cmd in matched_commands:
            args = ' '.join(msg[1:]).split()  # Convert list to string and then split
            if not commands[cmd].run(args):
                # clear_terminal()
                print("Command failed to execute... Please try again!")
    else:
        # clear_terminal()
        print("Checking rest of message for command")
        time.sleep(1)
        # clear_terminal()


def clear_terminal():
    return os.system('cls')
