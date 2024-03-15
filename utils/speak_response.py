import pyttsx3 as tts
import os
from elevenlabs import voices, generate, play, set_api_key
import commands.connected as connected


def load_elevenlabs_api_key(file_path):
    if not os.path.exists(file_path):
        with open(file_path, 'w'):  # Create the file if it doesn't exist
            pass

    with open(file_path, 'r') as file:
        api_key = file.read().strip()

    if not api_key:
        raise ValueError(
            f"API key file '{file_path}' is empty. Please add your ElevenLabs API key to the file.")

    return api_key


# Load the API key from the file
api_key_file = 'ELapi_key.txt'
elevenLabsAPIKey = load_elevenlabs_api_key(api_key_file)

# Set the API key
user = set_api_key(elevenLabsAPIKey)


def speak_response(response):
    if connected.is_connected():
        try:
            print("Using Eleven labs for speech")
            audio = generate(
                text=response,
                voice="9MHPRsXjcQrLl0zd1ZLU",
                model="eleven_monolingual_v1"
            )
            play(audio)
        except Exception as e:
            print(f"Error using Eleven Labs: {e}")
            use_pyttsx3(response)
    else:
        use_pyttsx3(response)


def use_pyttsx3(message):
    print("Using pyttsx3 for speech as a fallback")
    # Initialize text-to-speech
    speaker = tts.init()
    voices = speaker.getProperty('voices')
    # Set how fast it will talk.
    speaker.setProperty("rate", 175)
    speaker.setProperty('voice', voices[0].id)
    speaker.say(message)
    speaker.runAndWait()
