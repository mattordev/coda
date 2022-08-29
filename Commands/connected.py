import socket
import random

def run(args):
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
        print(random.choice(['We are connected', 'There is an established data connection']))
        return True
    else:
        return False