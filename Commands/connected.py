import socket
import random
from importlib.machinery import SourceFileLoader
import sys
import os
import utils.speak_response as speak


def run():
    REMOTE_SERVER = "www.google.com"
    
    def is_connected():
        try:
            host = socket.gethostbyname(REMOTE_SERVER)
            s = socket.create_connection((host, 80), 2)
            return True
        except:
            pass
        return False

    if(is_connected()):
        response = random.choice(
            ['We are connected', 'There is an established data connection'])
        print(response)
        speak.speak_response(response)
        return True
    else:
        return False

