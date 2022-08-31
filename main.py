# Imports
import os
import sys
from importlib.machinery import SourceFileLoader
import speech_recognition as sr
import utils.speak_response as speak


def clear_terminal():
    return
    return os.system('cls')


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


def display_message(message):
    print(message, flush=True)


### MAIN ###
## This loops, asking users to input a command here after saying the keyword. ##
wakeword = {"coda", "kodak", "coder", "skoda"}

while True:
    r = sr.Recognizer()
    with sr.Microphone() as source:
        #r.adjust_for_ambient_noise(source, duration=0.5)
        print('Ready to accept commands')
        try:
            audio = r.listen(source, timeout=0.1)
            speech = (r.recognize_google(audio, language="en-GB"))
            message = (speech.lower())
            display_message(message)

            if not wakeword.isdisjoint(message.lower().split()):
                on_command(str(message))

        # exceptions
        except sr.UnknownValueError:
            print("ERROR: Audio not understandable")
            continue
        except sr.RequestError as e:
            speak.speak_response(
                "There was an issue with that request. Please try again later.")
        except sr.WaitTimeoutError:
            continue

    # find out whats been said(Google Speech Recognition)
