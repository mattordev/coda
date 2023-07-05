import os
import platform
import time
# import memory_profiler as profiler
import utils.speak_response as speak
import utils.speak_response_with_input as speak_input

# TODO: have the program ask if the user wants to know about the system status, or the program status.
# Also implement someway to check the system health (temp, power usage etc.)


def run(args):
    speak.speak_response("Generating system report now...")
    # ask for the type of check
    # check(type)
    get_running_on_system_status()


# Gets the status of the machine or system that CODA is running on.
def get_running_on_system_status():
    print("This system is running on: " + platform.system())
    speak.speak_response("This system is running on: " + platform.system())

    time.sleep(.5)

    print("This system has a: " + platform.processor() + " processor.")
    speak.speak_response("This system has an: " +
                         platform.processor() + " processor.")

    time.sleep(.5)

    print("This system architecture is: ")
    print(platform.architecture())
    speak.speak_response("This systems architecture is: ")
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
