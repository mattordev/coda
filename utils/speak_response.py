import socket
import os
from threading import Event

import requests
from ai.providers.cancellable import CancellationScope
import utils.dashboard_state as dashboard_state
import utils.runtime_state as runtime_state
from runtime.speech_playback import SpeechProvider
from tts.elevenlabs_provider import ElevenLabsProvider
from tts.pyttsx3_provider import Pyttsx3Provider
from tts import registry as tts_registry
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
        load_api_key()
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

    return is_tts_available()


def is_connected():
    remote_server = "www.google.com"

    try:
        host = socket.gethostbyname(remote_server)
        with socket.create_connection((host, 80), 2):
            return True
    except OSError:
        return False


def is_tts_available():
    """Return whether a configured TTS provider is available."""
    for provider in resolve_speech_providers():
        try:
            if provider.is_available():
                return True
        except Exception as error:
            runtime_state.debug_print(
                f"[TTS] Provider {provider.name} availability check "
                f"failed: {error}"
            )

    return False
    
def _generate_elevenlabs_audio(
    response: str,
    scope: CancellationScope | None = None,
    timeout_seconds: float = 30.0,
) -> bytes:
    global _eleven_labs_disabled
    
    if not ensure_api_key_loaded():
        raise RuntimeError("ElevenLabs is not configured.")
    
    runtime_state.debug_print("Using Eleven labs for speech")
    active_scope = scope or CancellationScope()
    session = requests.Session()
    close_session = active_scope.add(session.close)
    
    try:
        with session.post(
            "https://api.elevenlabs.io/v1/text-to-speech/"
            "N2lVS1w4EtoT3dr4eOWO",
            headers={
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": load_api_key(),
            },
            json={
                "text": response,
                "model_id": "eleven_flash_v2_5",
            },
            timeout=timeout_seconds,
            stream=True,
        ) as provider_response:
            close_response = active_scope.add(provider_response.close)
            if provider_response.status_code == 401:
                _eleven_labs_disabled = True
            provider_response.raise_for_status()
            audio = b"".join(provider_response.iter_content(chunk_size=8192))
            close_response()
            return audio
    except requests.RequestException as error:
        error_response = getattr(error, "response", None)
        if getattr(error_response, "status_code", None) == 401:
            _eleven_labs_disabled = True
        raise
    finally:
        close_session()
    
def _resolve_elevenlabs_provider() -> SpeechProvider | None:
    global _elevenlabs_provider

    if not is_connected() or not ensure_api_key_loaded():
        return None

    if _elevenlabs_provider is None:
        _elevenlabs_provider = ElevenLabsProvider(
            _generate_elevenlabs_audio
        )

    return _elevenlabs_provider


def _resolve_pyttsx3_provider() -> SpeechProvider:
    return _pyttsx3_provider


_TTS_PROVIDER_RESOLVERS: tts_registry.ProviderResolvers = {
    "elevenlabs": _resolve_elevenlabs_provider,
    "pyttsx3": _resolve_pyttsx3_provider,
}


def resolve_speech_providers() -> list[SpeechProvider]:
    provider_names = tts_registry.parse_provider_order(
        os.getenv("TTS_PROVIDER_ORDER")
    )

    return tts_registry.resolve_providers(
        provider_names,
        _TTS_PROVIDER_RESOLVERS,
    )

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

    provider = _resolve_elevenlabs_provider()

    if provider is not None:
        try:
            session = provider.create_session(response)
            if session.play(Event()):
                return True

            return use_pyttsx3(response)
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

