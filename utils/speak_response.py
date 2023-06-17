import pyttsx3 as tts


def speak_response(response):
    
    # Initialize text-to-speech
    speaker = tts.init()
    voices = speaker.getProperty('voices')
    # Set how fast it will talk.
    speaker.setProperty("rate", 175)
    speaker.setProperty('voice', voices[0].id)
    speaker.say(response)
    speaker.runAndWait()
