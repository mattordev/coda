#import and other stuff
import cmd
import os, sys
from importlib.machinery import SourceFileLoader
import speech_recognition as sr
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
def on_command(msg):
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
    




### MAIN ###
## This loops, asking users to input a command here ##

wakeword = "coda"

i=0
n=0
while True:
   while (i<1):
        r = sr.Recognizer()
        with sr.Microphone() as source:
            audio = r.adjust_for_ambient_noise(source)
            n=(n+1)     
            print('Ready to accept commands')
            audio = r.listen(source)
    #find out whats been said(Google Speech Recognition)
        try:
            s = (r.recognize_google(audio))
            message = (s.lower())
            
            if ('coda') in message:
                print(message)
                on_command(str(message))


        # exceptions
        except sr.UnknownValueError:
            print("Could not understand audio")
        except sr.RequestError as e:
            print("Could not request results; {0}".format(e))