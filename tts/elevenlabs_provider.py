import shutil
from pathlib import Path
from subprocess import DEVNULL, Popen, TimeoutExpired
from tempfile import NamedTemporaryFile
from threading import Event, Lock
from typing import Callable


AudioGenerator = Callable[[str], bytes]


class ElevenLabsProvider:
    """Create interruptible sessions from ElevenLabs audio."""

    name = "elevenlabs"

    def __init__(
        self,
        generate_audio: AudioGenerator,
        ffplay_path: str | None = None,
    ) -> None:
        self._generate_audio = generate_audio
        self._ffplay_path = ffplay_path or shutil.which("ffplay")

    def is_available(self) -> bool:
        return self._ffplay_path is not None

    def create_session(self, text: str):
        if self._ffplay_path is None:
            raise RuntimeError("ffplay is not available.")

        return ElevenLabsSession(
            text=text,
            generate_audio=self._generate_audio,
            ffplay_path=self._ffplay_path,
        )


class ElevenLabsSession:
    """Generate ElevenLabs audio and own its ffplay process."""

    def __init__(
        self,
        text: str,
        generate_audio: AudioGenerator,
        ffplay_path: str,
    ) -> None:
        self._text = text
        self._generate_audio = generate_audio
        self._ffplay_path = ffplay_path
        self._lock = Lock()
        self._process: Popen | None = None

    def play(self, cancel_event: Event) -> bool:
        audio = self._generate_audio(self._text)

        if cancel_event.is_set():
            return False

        with NamedTemporaryFile(
            suffix=".mp3",
            delete=False,
        ) as audio_file:
            audio_file.write(audio)
            audio_path = Path(audio_file.name)

        process = None

        try:
            process = Popen(
                [
                    self._ffplay_path,
                    "-autoexit",
                    "-nodisp",
                    "-loglevel",
                    "quiet",
                    str(audio_path),
                ],
                stdout=DEVNULL,
                stderr=DEVNULL,
            )

            with self._lock:
                self._process = process

            while process.poll() is None:
                if cancel_event.wait(timeout=0.05):
                    self.stop()
                    return False

            return process.returncode == 0
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

            audio_path.unlink(missing_ok=True)

    def stop(self) -> None:
        with self._lock:
            process = self._process

        if process is None or process.poll() is not None:
            return

        try:
            process.terminate()
            process.wait(timeout=1.0)
        except TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
        except OSError:
            pass