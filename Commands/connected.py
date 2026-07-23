import random
import socket

from ai.intents import Intent, IntentRequest
import utils.speak_response as speak

INTENT = Intent(
    name="connected",
    description="Checks if CODA can reach the internet.",
    aliases=(
        "connection",
        "connectivity",
        "online",
        "offline",
        "internet status",
        "network status",
    ),
)


def is_connected():
    REMOTE_SERVER = "www.google.com"

    try:
        host = socket.gethostbyname(REMOTE_SERVER)
        s = socket.create_connection((host, 80), 2)
        return True
    except:
        pass
    return False


def run(request: IntentRequest) -> bool:
    if (is_connected()):
        response = random.choice(
            ['We are connected.', 'There is an established data connection.', 'We are online and ready to go boss!', 'We are up and running, with consistent data streams.', 'Things are looking good here.', 'We\'re online and ready to go sir.'])
        print(response)
        speak.speak_response(response)
        return True
    else:
        response = random.choice(
            ['We are not connected.', 'There is no established data connection.', 'We are offline and need to connect.', 'We are down and need to get back online.', 'Things are not looking good here.', 'We\'re offline and need to go online sir.'])
        print(response)
        speak.speak_response(response)
        return False
