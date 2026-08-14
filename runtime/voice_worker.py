from threading import Event, Lock, Thread
from typing import Callable

class VoiceInputWorker:
    """Manages the lifecycle of CODA's voice-input thread."""
    
    def __init__(
        self,
        target: Callable[[Event], None],
        shutdown_event: Event,
    ):
        self._target = target
        self._shutdown_event = shutdown_event
        self._thread: Thread | None = None
        self._restart_requested = False
        self._lock = Lock()
        
    def start(self) -> None:
        """Start the voice-input worker if it's not already started"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._shutdown_event.is_set():
                    self._restart_requested = True
                return

            self._restart_requested = False
            self._shutdown_event.clear()
            self._thread = Thread(
                target=self._run,
                name="coda-voice-input",
                daemon=True,
            )
            self._thread.start()
        
    def stop(self, timeout: float = 4.0) -> None:
        """Signal the voice-input worker to stop and wait for it."""
        with self._lock:
            self._restart_requested = False
            self._shutdown_event.set()
            thread = self._thread

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            
    def is_alive(self) -> bool:
        """Return whether the voice-input thread is running."""
        with self._lock:
            thread = self._thread

        return thread is not None and thread.is_alive()

    def _run(self) -> None:
        """Run the listener and honour a restart requested while stopping."""
        while True:
            self._target(self._shutdown_event)

            with self._lock:
                if self._restart_requested:
                    self._restart_requested = False
                    self._shutdown_event.clear()
                    continue

                self._thread = None
                return
