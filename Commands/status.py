import os
import platform
import time
#import memory_profiler as profiler
import utils.speak_response as speak


def run(args):
    type = input(speak.speak_response("System status or program status?"))
    # ask for the type of check
    check(type)


# Gets the status of the machine or system that CODA is running on.
def get_running_on_system_status():
    print("This system is running on:" + platform.system())
    speak.speak_response("This system is running on:" + platform.system())
    
    time.sleep(.5)
    
    print("This system has a: " + platform.processor())
    speak.speak_response("This system has a: " + platform.processor())
    
    time.sleep(.5)
    
    print("This system architecture is: ")
    print(platform.architecture())
    speak.speak_response("This system architecture is: ")
    speak.speak_response(platform.architecture())
    
    time.sleep(.5)
    
    print("This system machine type is: " + platform.machine())
    speak.speak_response("This system machine type is: " + platform.machine())
    
# Gets the status of the program.
def get_program_status():
    print("not yet avaialable")
    
def check(type):
    type.lower()
    
    if type == "system":
        get_running_on_system_status()
    else:
        get_program_status()