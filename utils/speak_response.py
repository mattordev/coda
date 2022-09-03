import pyttsx3 as tts


def speak_response(response):
    # Initialize text-to-speech
    speaker = tts.init()
    # Set how fast it will talk.
    speaker.setProperty("rate", 175)
    speaker.setProperty('voice', voices[0].id)
    speaker.say(response)
    speaker.runAndWait()
    
    return response
