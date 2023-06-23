import socket
import random
from importlib.machinery import SourceFileLoader
import sys
import os
import utils.speak_response as speak


def is_connected():
    REMOTE_SERVER = "www.google.com"

    try:
        host = socket.gethostbyname(REMOTE_SERVER)
        s = socket.create_connection((host, 80), 2)
        return True
    except:
        pass
    return False


def run(args):
    if (is_connected()):
        response = random.choice(
            ['We are connected', 'There is an established data connection', 'We are online and ready to go boss', 'We are up and running, with consistent data streams', 'Things are looking good here.', 'We\'re online and ready to go sir'])
        print(response)
        speak.speak_response(response)
        return True
    else:
        return False
