import socket
import os
from pathlib import Path

from elevenlabs import generate, play, set_api_key

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

_api_key_loaded = False
_eleven_labs_disabled = False

if load_dotenv is not None:
    load_dotenv()


def _candidate_key_paths():
    repo_root = Path(__file__).resolve().parent.parent
    return [
        repo_root / "ELapi_key.txt",
        repo_root / "ELapikey.txt",
    ]


def load_api_key():
    # Preferred source is .env for local secret management.
    env_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if env_key:
        return env_key

    for path in _candidate_key_paths():
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(
        "Could not find ELEVENLABS_API_KEY in environment or key file. "
        "Expected one of: "
        + ", ".join(str(p) for p in _candidate_key_paths())
    )


def ensure_api_key_loaded():
    global _api_key_loaded
    global _eleven_labs_disabled

    if _eleven_labs_disabled:
        return False


def reload_config():
    global _api_key_loaded
    global _eleven_labs_disabled

    if load_dotenv is not None:
        load_dotenv(override=True)

    _api_key_loaded = False
    _eleven_labs_disabled = False

    if _api_key_loaded:
        return True

    try:
        eleven_labs_api_key = load_api_key()
        set_api_key(eleven_labs_api_key)
        _api_key_loaded = True
        return True
    except Exception as error:
        print(f"Unable to initialize ElevenLabs API key: {error}")
        _eleven_labs_disabled = True
        return False


def is_connected():
    remote_server = "www.google.com"

    try:
        host = socket.gethostbyname(remote_server)
        with socket.create_connection((host, 80), 2):
            return True
    except OSError:
        return False


def speak_response(response):
    global _eleven_labs_disabled

    if is_connected() and ensure_api_key_loaded():
        try:
            print("Using Eleven labs for speech")
            audio = generate(
                text=response,
                voice="9MHPRsXjcQrLl0zd1ZLU",
                model="eleven_monolingual_v1",
            )
            play(audio)
            return True
        except Exception as e:
            print(f"Error using Eleven Labs: {e}")

            if "invalid api key" in str(e).lower():
                _eleven_labs_disabled = True

            return use_pyttsx3(response)

    return use_pyttsx3(response)


def use_pyttsx3(message):
    print("Using pyttsx3 for speech as a fallback")

    try:
        import pyttsx3 as tts

        speaker = tts.init()
        available_voices = speaker.getProperty("voices")
        speaker.setProperty("rate", 175)

        if available_voices:
            speaker.setProperty("voice", available_voices[0].id)

        speaker.say(message)
        speaker.runAndWait()
        return True
    except Exception as error:
        print(f"Error using pyttsx3 fallback: {error}")
        print(f"TTS disabled, response text: {message}")
        return False
