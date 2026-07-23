import platform
import time
# import memory_profiler as profiler

from ai.intents import Intent, IntentRequest
import utils.speak_response as speak


INTENT = Intent(
    name="status",
    description="Report information about the system running CODA.",
    aliases=(
        "system status",
        "system information",
        "system report",
    ),
)

# TODO: have the program ask if the user wants to know about the system status, or the program status.
# Also implement someway to check the system health (temp, power usage etc.)


def run(request: IntentRequest) -> bool:
    speak.speak_response("Generating system report now...")
    get_running_on_system_status()
    return True


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
