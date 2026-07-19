import os
import platform
import shutil
import tempfile

import speech_recognition as sr
import utils.runtime_state as runtime_state

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None


if load_dotenv is not None:
    load_dotenv()


whisper_model_cache = {}
failed_whisper_attempts = {}


class SpeechToTextError(Exception):
    pass


def _get_bool_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _get_int_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


def _normalize_provider_name(provider):
    aliases = {
        "whisper": "faster-whisper",
        "faster_whisper": "faster-whisper",
        "fasterwhisper": "faster-whisper",
    }
    return aliases.get(provider, provider)


def reload_config():
    global whisper_model_cache
    global failed_whisper_attempts

    if load_dotenv is not None:
        load_dotenv(override=True)

    whisper_model_cache = {}
    failed_whisper_attempts = {}


def _cuda_available():
    return shutil.which("nvidia-smi") is not None


def _get_stt_provider():
    provider = _normalize_provider_name(
        os.getenv("CODA_STT_PROVIDER", "auto").strip().lower()
    )

    if provider in ("", "auto"):
        if WhisperModel is not None:
            return "faster-whisper"
        return "google"

    return provider


def get_stt_provider():
    return _get_stt_provider()


def _get_whisper_language():
    language = os.getenv("CODA_WHISPER_LANGUAGE", "").strip()
    return language or None


def _get_whisper_device():
    configured_device = os.getenv("CODA_WHISPER_DEVICE", "").strip().lower()
    if configured_device:
        return configured_device

    if _cuda_available():
        return "cuda"

    return "cpu"


def _get_whisper_compute_type(device=None):
    configured_compute_type = os.getenv("CODA_WHISPER_COMPUTE_TYPE", "").strip().lower()
    if configured_compute_type:
        return configured_compute_type

    if (device or _get_whisper_device()) == "cuda":
        return "float16"

    return "int8"


def _get_whisper_model():
    configured_model = os.getenv("CODA_WHISPER_MODEL", "").strip()
    if configured_model and configured_model.lower() != "auto":
        return configured_model

    machine = platform.machine().lower()
    device = _get_whisper_device()

    if device == "cuda":
        return "turbo"

    if any(arm_name in machine for arm_name in ("arm", "aarch64")):
        return "base"

    return "small"


def _load_whisper_model(model_name, device, compute_type):
    if WhisperModel is None:
        raise SpeechToTextError(
            "faster-whisper is not installed. "
            "Install the dependency or set CODA_STT_PROVIDER=google."
        )

    cache_key = (model_name, device, compute_type)

    if cache_key not in whisper_model_cache:
        whisper_model_cache[cache_key] = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )

    return whisper_model_cache[cache_key]


def _get_failed_whisper_attempt_message(attempt):
    return failed_whisper_attempts.get(attempt)


def _remember_failed_whisper_attempt(attempt, exc):
    failed_whisper_attempts[attempt] = str(exc)


def _get_available_whisper_attempts():
    attempts = _get_whisper_attempts()
    available_attempts = []

    for attempt in attempts:
        if _get_failed_whisper_attempt_message(attempt) is None:
            available_attempts.append(attempt)

    return available_attempts


def _get_whisper_attempts():
    configured_model = os.getenv("CODA_WHISPER_MODEL", "").strip()
    configured_device = os.getenv("CODA_WHISPER_DEVICE", "").strip().lower()
    preferred_device = _get_whisper_device()
    machine = platform.machine().lower()
    is_arm = any(arm_name in machine for arm_name in ("arm", "aarch64"))

    attempts = []

    def add_attempt(model_name, device):
        attempt = (model_name, device, _get_whisper_compute_type(device=device))
        if attempt not in attempts:
            attempts.append(attempt)

    if configured_model and configured_model.lower() != "auto":
        model_name = configured_model
        add_attempt(model_name, preferred_device)
        if not configured_device and preferred_device != "cpu":
            add_attempt(model_name, "cpu")
        return attempts

    if configured_device:
        add_attempt(_get_whisper_model(), preferred_device)
        if preferred_device == "cpu":
            add_attempt("base" if is_arm else "small", "cpu")
        return attempts

    if preferred_device == "cuda":
        add_attempt("turbo", "cuda")
        add_attempt("small", "cpu")
        add_attempt("base", "cpu")
        return attempts

    if is_arm:
        add_attempt("base", "cpu")
        add_attempt("small", "cpu")
        return attempts

    add_attempt("small", "cpu")
    add_attempt("base", "cpu")
    return attempts


def _write_audio_to_temp_file(audio):
    wav_data = audio.get_wav_data(convert_rate=16000, convert_width=2)
    temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)

    try:
        temp_file.write(wav_data)
        return temp_file.name
    finally:
        temp_file.close()


def _transcribe_with_faster_whisper(audio):
    audio_file_path = _write_audio_to_temp_file(audio)
    beam_size = _get_int_env("CODA_WHISPER_BEAM_SIZE", 5)
    whisper_language = _get_whisper_language()
    vad_filter = _get_bool_env("CODA_WHISPER_VAD_FILTER", True)
    last_error = None
    attempts = _get_available_whisper_attempts()

    if not attempts:
        raise SpeechToTextError(
            "all faster-whisper attempts are disabled for this session. "
            "Run 'debug reload' after changing your Whisper setup."
        )

    try:
        transcription_options = {
            "beam_size": beam_size,
            "condition_on_previous_text": False,
            "vad_filter": vad_filter,
        }
        if whisper_language:
            transcription_options["language"] = whisper_language

        for index, (model_name, device, compute_type) in enumerate(attempts):
            current_attempt = (model_name, device, compute_type)
            try:
                model = _load_whisper_model(model_name, device, compute_type)
                segments, _ = model.transcribe(audio_file_path, **transcription_options)
                text = " ".join(
                    segment.text.strip()
                    for segment in segments
                    if segment.text and segment.text.strip()
                ).strip()
                if text:
                    return text
                last_error = sr.UnknownValueError()
            except Exception as exc:
                last_error = exc
                _remember_failed_whisper_attempt(current_attempt, exc)
                if index + 1 < len(attempts):
                    next_model, next_device, _ = attempts[index + 1]
                    runtime_state.debug_print(
                        "[VOICE] faster-whisper attempt failed "
                        f"({model_name} on {device}/{compute_type}). "
                        f"Trying {next_model} on {next_device}: {exc}"
                    )
    finally:
        try:
            os.unlink(audio_file_path)
        except OSError:
            pass

    if isinstance(last_error, sr.UnknownValueError):
        raise sr.UnknownValueError()

    if last_error is not None:
        raise last_error

    raise SpeechToTextError("faster-whisper returned no transcript")


def _transcribe_with_google(recognizer, audio):
    return recognizer.recognize_google(audio).strip()


def describe_stt_provider():
    provider = _get_stt_provider()

    if provider == "google":
        return "google speech recognition"

    if provider == "faster-whisper":
        attempts = _get_whisper_attempts()
        model_name, device, compute_type = attempts[0]
        local_fallback = ""
        if len(attempts) > 1:
            fallback_model, fallback_device, fallback_compute = attempts[1]
            local_fallback = (
                f"; local fallback: {fallback_model} on {fallback_device}/{fallback_compute}"
            )
        disabled_attempts = []
        for attempt in attempts:
            failure_message = _get_failed_whisper_attempt_message(attempt)
            if failure_message is None:
                continue
            failed_model, failed_device, failed_compute = attempt
            disabled_attempts.append(
                f"{failed_model} on {failed_device}/{failed_compute}"
            )
        disabled_text = ""
        if disabled_attempts:
            disabled_text = f"; session-disabled: {', '.join(disabled_attempts)}"
        return (
            "faster-whisper "
            f"(preferred: {model_name} on {device}/{compute_type}"
            f"{local_fallback}{disabled_text}; fallback: google)"
        )

    return provider


def transcribe_audio(recognizer, audio):
    provider = _get_stt_provider()

    if provider not in ("google", "faster-whisper"):
        raise SpeechToTextError(
            f"unsupported CODA_STT_PROVIDER '{provider}'. "
            "Expected 'auto', 'whisper', 'faster-whisper', or 'google'."
        )

    provider_chain = [provider]
    if provider == "faster-whisper":
        provider_chain.append("google")

    last_error = None

    for index, current_provider in enumerate(provider_chain):
        try:
            if current_provider == "faster-whisper":
                return _transcribe_with_faster_whisper(audio), current_provider

            return _transcribe_with_google(recognizer, audio), current_provider
        except sr.UnknownValueError as exc:
            last_error = exc
        except sr.RequestError as exc:
            last_error = exc
            if index + 1 < len(provider_chain):
                next_provider = provider_chain[index + 1]
                runtime_state.debug_print(
                    f"[VOICE] {current_provider} recognition failed. "
                    f"Falling back to {next_provider}: {exc}"
                )
        except Exception as exc:
            last_error = SpeechToTextError(str(exc))
            if index + 1 < len(provider_chain):
                next_provider = provider_chain[index + 1]
                runtime_state.debug_print(
                    f"[VOICE] {current_provider} recognition failed. "
                    f"Falling back to {next_provider}: {exc}"
                )

    if last_error is not None:
        raise last_error

    raise SpeechToTextError("speech recognition failed without a captured error")
