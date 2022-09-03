import os
import platform
import time
#import memory_profiler as profiler
import utils.speak_response as speak


def run():
    # ask for the type of check
    check(1)


# Gets the status of the machine or system that CODA is running on.
def get_running_on_system_status():
    speak.speak_response("This system is running on:" + platform.system())
    print("This system is running on:" + platform.system())
    
    time.sleep(.5)
    
    speak.speak_response("This system has a: " + platform.processor())
    print("This system has a: " + platform.processor())
    
    time.sleep(.5)
    
    speak.speak_response("This system architecture is: " + platform.architecture())
    print("This system architecture is: " + platform.architecture())
    
    time.sleep(.5)
    
    speak.speak_response("This system machine type is: " + platform.machine())
    print("This system machine type is: " + platform.machine())
    
# Gets the status of the program.
def get_program_status():
    print("not yet avaialable")
    
def check(type):
    if type == 1:
        get_running_on_system_status()
    else:
        get_program_status()