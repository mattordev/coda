import pyttsx3 as tts
from elevenlabs import generate, play, stream, set_api_key
import commands.connected as connected

elevenLabsAPIKey = 'abe3e7684e46c45769406e9f76f5f936'

user = set_api_key(elevenLabsAPIKey)


def speak_response(response):

    if connected.is_connected():
        print("Using Eleven labs for speech")
        audio = generate(
            text=response,
            voice="Rachel",
            model="eleven_monolingual_v1"
        )
        play(audio)
    else:
        print("using pyttsx3 for speech")
        # Initialize text-to-speech
        speaker = tts.init()
        voices = speaker.getProperty('voices')
        # Set how fast it will talk.
        speaker.setProperty("rate", 175)
        speaker.setProperty('voice', voices[0].id)
        speaker.say(response)
        speaker.runAndWait()
