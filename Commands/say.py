import utils.speak_response as speak

def run(args):
    args = " ".join(str(e)  for e in args)
    speak.speak_response(args)
    return True