import utils.speak_response as speak


def run(args):
    # Skips the first arg, which will always be the word "say"
    args = " ".join(str(e) for e in args[1:])
    speak.speak_response(args)
    return True
