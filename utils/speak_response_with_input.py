# very similar in function to speak_response.py, except, this one waits for an input after speaking the response
import pyttsx3 as tts
import utils.voice_recognizer as voice_recognizer


def speak_response(response):
    # Initialize text-to-speech
    speaker = tts.init()
    # Set how fast it will talk.
    speaker.setProperty("rate", 175)
    speaker.setProperty('voice', voices[0].id)
    speaker.say(response)
    speaker.runAndWait()
    
    
    
    #voice_recognizer.run(wakeword, commands, type)
