import socket
import os

from elevenlabs import generate, play, set_api_key
import utils.dashboard_state as dashboard_state
import utils.runtime_state as runtime_state
from runtime.speech_playback import SpeechProvider
from tts.elevenlabs_provider import ElevenLabsProvider
from tts.pyttsx3_provider import Pyttsx3Provider
from typing import Callable

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

_api_key_loaded = False
_eleven_labs_disabled = False
_elevenlabs_provider: ElevenLabsProvider | None = None
_pyttsx3_provider = Pyttsx3Provider()

SpeechSubmitter = Callable[[str], bool]

_speech_submitter: SpeechSubmitter | None = None

if load_dotenv is not None:
    load_dotenv()



def load_api_key():
    env_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if env_key:
        return env_key

    raise FileNotFoundError("Could not find ELEVENLABS_API_KEY in environment.")


def ensure_api_key_loaded():
    global _api_key_loaded
    global _eleven_labs_disabled

    if _eleven_labs_disabled:
        return False

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


def reload_config():
    global _api_key_loaded
    global _eleven_labs_disabled
    global _elevenlabs_provider

    if load_dotenv is not None:
        load_dotenv(override=True)

    _api_key_loaded = False
    _eleven_labs_disabled = False
    _elevenlabs_provider = None

    return ensure_api_key_loaded()


def is_connected():
    remote_server = "www.google.com"

    try:
        host = socket.gethostbyname(remote_server)
        with socket.create_connection((host, 80), 2):
            return True
    except OSError:
        return False


def is_tts_available():
    """Return whether a configured cloud or local TTS path is available."""
    has_eleven_labs = (
        not _eleven_labs_disabled
        and bool(os.getenv("ELEVENLABS_API_KEY", "").strip())
        and is_connected()
    )
    if has_eleven_labs:
        return True

    try:
        import pyttsx3 as tts

        speaker = tts.init()
        speaker.stop()
        return True
    except Exception:
        return False
    
def _generate_elevenlabs_audio(response: str) -> bytes:
    global _eleven_labs_disabled
    
    if not ensure_api_key_loaded():
        raise RuntimeError("ElevenLabs is not configured.")
    
    runtime_state.debug_print("Using Eleven labs for speech")
    
    try:
        return generate(
            text=response,
            voice="N2lVS1w4EtoT3dr4eOWO",
            model="eleven_flash_v2_5",
        )
    except Exception as error:
        if "invalid api key" in str(error).lower():
            _eleven_labs_disabled = True

        raise
    
def resolve_speech_providers() -> list[SpeechProvider]:
    global _elevenlabs_provider
    
    providers: list[SpeechProvider] = []
    
    if is_connected() and ensure_api_key_loaded():
        if _elevenlabs_provider is None:
            _elevenlabs_provider = ElevenLabsProvider(
                _generate_elevenlabs_audio
            )
            
        providers.append(_elevenlabs_provider)
    
    providers.append(_pyttsx3_provider)
    return providers

def configure_speech_submitter(
    submitter: SpeechSubmitter | None,
) -> None:
    """Configure asynchronous speech submission for the runtime."""
    global _speech_submitter

    _speech_submitter = submitter

def speak_response(response):
    global _eleven_labs_disabled

    dashboard_state.record_ai_response(response, source="tts")

    if _speech_submitter is not None:
        return _speech_submitter(response)

    if is_connected() and ensure_api_key_loaded():
        try:
            audio = _generate_elevenlabs_audio(response)
            
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

