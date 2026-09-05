import os
import re
import shutil
from collections.abc import Callable, Iterator
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired
from threading import Event, Lock
from typing import Protocol

import utils.runtime_state as runtime_state


class PocketTTSRuntime(Protocol):
    sample_rate: int

    def stream_pcm(self, text: str) -> Iterator[bytes]:
        """Yield mono float32 PCM chunks for one response."""
        ...


RuntimeFactory = Callable[[str, str | None], PocketTTSRuntime]
PLAYBACK_TAIL_SECONDS = 0.2
POCKET_TOKEN_LIMIT = 50
POCKET_CHUNK_TARGET = 45


def _split_for_token_limit(
    text: str,
    count_tokens: Callable[[str], int],
    target_tokens: int = POCKET_CHUNK_TARGET,
) -> list[str]:
    """Split oversized Pocket input at words with token-count headroom."""
    if count_tokens(text) <= POCKET_TOKEN_LIMIT:
        return [text]

    words = re.findall(r"\S+", text)
    chunks = []
    current_words = []

    for word in words:
        candidate_words = [*current_words, word]
        candidate = " ".join(candidate_words)

        if current_words and count_tokens(candidate) > target_tokens:
            boundary_index = None
            for index in range(len(current_words) - 1, -1, -1):
                if not current_words[index].rstrip("\"')]").endswith(
                    (",", ";", ":", "\N{EM DASH}", "\N{EN DASH}")
                ):
                    continue

                remainder = [*current_words[index + 1:], word]
                if count_tokens(" ".join(remainder)) <= target_tokens:
                    boundary_index = index
                    break

            if boundary_index is None:
                chunks.append(" ".join(current_words))
                current_words = [word]
            else:
                chunks.append(" ".join(current_words[:boundary_index + 1]))
                current_words = [*current_words[boundary_index + 1:], word]
        else:
            current_words = candidate_words

    if current_words:
        chunks.append(" ".join(current_words))

    return chunks


class _LoadedPocketTTSRuntime:
    def __init__(self, model, voice_state) -> None:
        self._model = model
        self._voice_state = voice_state
        self._generation_lock = Lock()
        self.sample_rate = model.sample_rate

    def stream_pcm(self, text: str) -> Iterator[bytes]:
        with self._generation_lock:
            tokenizer = self._model.flow_lm.conditioner.tokenizer
            count_tokens = lambda value: len(
                tokenizer(value).tokens[0].tolist()
            )

            for text_chunk in _split_for_token_limit(text, count_tokens):
                for audio_chunk in self._model.generate_audio_stream(
                    self._voice_state,
                    text_chunk,
                ):
                    yield (
                        audio_chunk.detach()
                        .cpu()
                        .numpy()
                        .astype("<f4", copy=False)
                        .tobytes()
                    )


def _load_runtime(language: str, voice: str | None) -> PocketTTSRuntime:
    from pocket_tts import TTSModel
    from pocket_tts.default_parameters import get_default_voice_for_language

    model = TTSModel.load_model(
        language=language,
        quantize=False,
    )
    selected_voice = voice or get_default_voice_for_language(language)
    voice_state = model.get_state_for_audio_prompt(selected_voice)
    return _LoadedPocketTTSRuntime(model, voice_state)


def _env_setting(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _optional_env_setting(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


class PocketTTSProvider:
    """Retain one PocketTTS model and create streaming speech sessions."""

    name = "pockettts"

    def __init__(
        self,
        runtime_factory: RuntimeFactory = _load_runtime,
        ffplay_path: str | None = None,
        language: str | None = None,
        voice: str | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._ffplay_path = ffplay_path or shutil.which("ffplay")
        self._language = language or _env_setting(
            "CODA_POCKET_TTS_LANGUAGE",
            "english",
        )
        self._voice = voice or _optional_env_setting(
            "CODA_POCKET_TTS_VOICE"
        )
        self._lock = Lock()
        self._runtime: PocketTTSRuntime | None = None
        self._load_error: Exception | None = None
        self._closed = False

    def is_available(self) -> bool:
        if self._ffplay_path is None:
            return False

        try:
            self._get_runtime()
            return True
        except Exception as error:
            runtime_state.debug_print(
                f"[TTS] Pocket TTS is unavailable: {error}"
            )
            return False

    def create_session(self, text: str):
        if self._ffplay_path is None:
            raise RuntimeError("ffplay is not available.")

        return PocketTTSSession(
            text=text,
            runtime=self._get_runtime(),
            ffplay_path=self._ffplay_path,
        )

    def close(self) -> None:
        """Release retained model and failed-startup state."""
        with self._lock:
            self._closed = True
            self._runtime = None
            self._load_error = None

    def _get_runtime(self) -> PocketTTSRuntime:
        with self._lock:
            if self._closed:
                raise RuntimeError("Pocket TTS provider is closed.")

            if self._runtime is not None:
                return self._runtime

            if self._load_error is not None:
                raise RuntimeError(
                    "Pocket TTS initialization previously failed: "
                    f"{self._load_error}"
                ) from self._load_error

            try:
                self._runtime = self._runtime_factory(
                    self._language,
                    self._voice,
                )
            except Exception as error:
                self._load_error = error
                raise RuntimeError(
                    f"Pocket TTS could not be initialized: {error}"
                ) from error

            return self._runtime


class PocketTTSSession:
    """Stream PocketTTS PCM to ffplay and discard output after cancellation."""

    def __init__(
        self,
        text: str,
        runtime: PocketTTSRuntime,
        ffplay_path: str,
    ) -> None:
        self._text = text
        self._runtime = runtime
        self._ffplay_path = ffplay_path
        self._stop_event = Event()
        self._lock = Lock()
        self._process: Popen | None = None
        self._playing = False

    def play(self, cancel_event: Event) -> bool:
        if cancel_event.is_set() or self._stop_event.is_set():
            return False

        process = None
        cancelled = False
        playback_error = None

        try:
            with self._lock:
                if cancel_event.is_set() or self._stop_event.is_set():
                    return False
                self._playing = True

            process = Popen(
                [
                    self._ffplay_path,
                    "-autoexit",
                    "-nodisp",
                    "-loglevel",
                    "quiet",
                    "-f",
                    "f32le",
                    "-ar",
                    str(self._runtime.sample_rate),
                    "-ch_layout",
                    "mono",
                    "pipe:0",
                ],
                stdin=PIPE,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )

            with self._lock:
                self._process = process

            if self._should_stop(cancel_event):
                cancelled = True
                self._terminate_process(process)

            for pcm_chunk in self._runtime.stream_pcm(self._text):
                if self._should_stop(cancel_event):
                    if not cancelled:
                        self._terminate_process(process)
                    cancelled = True
                    continue

                if playback_error is not None:
                    continue

                try:
                    if process.stdin is None:
                        raise RuntimeError("ffplay input pipe is unavailable.")
                    process.stdin.write(pcm_chunk)
                    process.stdin.flush()
                except (BrokenPipeError, OSError) as error:
                    if self._should_stop(cancel_event):
                        cancelled = True
                    else:
                        playback_error = error

            if self._should_stop(cancel_event):
                self._terminate_process(process)
                return False

            if cancelled:
                return False

            if playback_error is not None:
                raise playback_error

            silence_samples = int(
                self._runtime.sample_rate * PLAYBACK_TAIL_SECONDS
            )
            if process.stdin is None:
                raise RuntimeError("ffplay input pipe is unavailable.")
            process.stdin.write(bytes(silence_samples * 4))
            process.stdin.flush()

            self._close_stdin(process)

            while process.poll() is None:
                if cancel_event.wait(timeout=0.05) or self._stop_event.is_set():
                    self._terminate_process(process)
                    return False

            return process.returncode == 0
        finally:
            if process is not None:
                if process.poll() is None:
                    self._terminate_process(process)
                self._close_stdin(process)

            with self._lock:
                if self._process is process:
                    self._process = None
                self._playing = False

    def stop(self) -> None:
        self._stop_event.set()

        with self._lock:
            process = self._process

        if process is not None:
            self._terminate_process(process)

        with self._lock:
            self._playing = False

    def is_playing(self) -> bool:
        """Return whether ffplay is starting or producing PocketTTS audio."""
        with self._lock:
            return self._playing

    def _should_stop(self, cancel_event: Event) -> bool:
        return cancel_event.is_set() or self._stop_event.is_set()

    @staticmethod
    def _close_stdin(process: Popen) -> None:
        if process.stdin is None:
            return

        try:
            process.stdin.close()
        except OSError:
            pass

    @staticmethod
    def _terminate_process(process: Popen) -> None:
        if process.poll() is not None:
            return

        try:
            process.terminate()
            process.wait(timeout=1.0)
        except TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
        except OSError:
            pass
