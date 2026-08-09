from threading import Event, Lock
from typing import Callable, Protocol


class Pyttsx3Engine(Protocol):
    def getProperty(self, name: str):
        ...

    def setProperty(self, name: str, value) -> None:
        ...

    def say(self, text: str) -> None:
        ...

    def startLoop(self, useDriverLoop: bool) -> None:
        ...

    def iterate(self) -> None:
        ...

    def isBusy(self) -> bool:
        ...

    def stop(self) -> None:
        ...

    def endLoop(self) -> None:
        ...


EngineFactory = Callable[[], Pyttsx3Engine]


def _create_default_engine() -> Pyttsx3Engine:
    import pyttsx3

    return pyttsx3.init()


class Pyttsx3Provider:
    """Create interruptible sessions using one retained pyttsx3 engine."""

    name = "pyttsx3"

    def __init__(
        self,
        engine_factory: EngineFactory = _create_default_engine,
        rate: int = 175,
    ) -> None:
        self._engine_factory = engine_factory
        self._rate = rate
        self._lock = Lock()
        self._engine: Pyttsx3Engine | None = None

    def is_available(self) -> bool:
        try:
            self._get_engine()
            return True
        except Exception:
            return False

    def create_session(self, text: str):
        return Pyttsx3Session(
            text=text,
            engine=self._get_engine(),
        )

    def _get_engine(self) -> Pyttsx3Engine:
        with self._lock:
            if self._engine is None:
                engine = self._engine_factory()
                voices = engine.getProperty("voices")

                engine.setProperty("rate", self._rate)

                if voices:
                    engine.setProperty("voice", voices[0].id)

                self._engine = engine

            return self._engine


class Pyttsx3Session:
    """Pump pyttsx3 manually so cancellation can be observed."""

    def __init__(
        self,
        text: str,
        engine: Pyttsx3Engine,
    ) -> None:
        self._text = text
        self._engine = engine
        self._stop_event = Event()

    def play(self, cancel_event: Event) -> bool:
        if cancel_event.is_set():
            return False

        self._stop_event.clear()
        self._engine.say(self._text)
        loop_started = False

        try:
            self._engine.startLoop(False)
            loop_started = True

            while True:
                self._engine.iterate()

                if (
                    cancel_event.is_set()
                    or self._stop_event.is_set()
                ):
                    self._engine.stop()
                    return False

                if not self._engine.isBusy():
                    return True

                self._stop_event.wait(timeout=0.01)
        finally:
            if loop_started:
                self._engine.endLoop()

    def stop(self) -> None:
        self._stop_event.set()
