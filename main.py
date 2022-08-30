# Imports
import os
import sys
from importlib.machinery import SourceFileLoader
from turtle import clear
import speech_recognition as sr
import pyttsx3 as tts



Clear = lambda: os.system('cls')
command_file_location = os.getcwd()+ "\Commands\\"
sys.path.append(command_file_location)

commands = {} # ALL COMMANDS TO BE USED BY OPERATOR
for cmdFile in os.listdir(command_file_location):
    name = os.fsdecode(cmdFile)       
    if(name.endswith(".py")):
        module = SourceFileLoader(cmdFile, command_file_location+cmdFile).load_module()
        commands[name.split(".py")[0].lower()] = module


### THIS FUNCTION PROCESSES COMMANDS FROM INPUTS PASSED INTO IT ###
def on_command_old(msg):
    if(len(msg) <= 0): return

    msg = msg.lower().split() # LOWERS AND SEPERATES EACH WORD INTO AN INDEXED ARRAY

    cmd = msg[0]    
    if(cmd in commands):
        args = [] # HOLDS THE PARAMETERS OF A COMMAND

        for i in range(1, len(msg)): # ITERATE THROUGH THE MESSAGE, IGNORING THE COMMAND INDEX
            args.append(msg[i])
        
        if(not commands[cmd].run(args)): # CALLS THE FUNCTION AND SIMULTANEOUSLY CHECKS TO SEE IF IT FAILED
            Clear()
            print("Command failed to execute... Please try again!")
            return                
    else:
        Clear()
        print("Command does not exist... Please try again!")

def on_command(msg):
    # Guard clause to stop empty messages.
    if (len(msg) <= 0): return 
    
    # Make the message lowercase, split it into an array.
    msg = msg.lower().split()
    
    for i in range(1, len(msg)):
        if (msg[i] in commands):
            args = [] # This holds any params of the command
            cmd = msg[i]
            if (not commands[cmd].run(args)):
                Clear()
                print("Command failed to execute... Please try again!")
        else:
            Clear()
            #print("Command does not exist... Please try again!")
            print ("Checking rest of messasge for command")
            Clear()

def display_message(message):
    print(message)

def speak_response(response):
    # Initialize text-to-speech
    speaker = tts.init
    # Set how fast it will talk.
    speaker.setProperty('rate', 200)
    speaker.say(response)



### MAIN ###
## This loops, asking users to input a command here after saying the keyword. ##

wakeword = "coda"

while True:
    r = sr.Recognizer()
    with sr.Microphone() as source:
        audio = r.adjust_for_ambient_noise(source)   
        print('Ready to accept commands')
        audio = r.listen(source)
#find out whats been said(Google Speech Recognition)
    try:
        speech = (r.recognize_google(audio))
        message = (speech.lower())
        
        
        if (wakeword) in message:
           display_message(message)
           on_command(str(message)) 


    # exceptions
    except sr.UnknownValueError:
        print("Could not understand audio")
    except sr.RequestError as e:
        print("Could not request results; {0}".format(e))