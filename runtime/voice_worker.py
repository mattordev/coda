from threading import Event, Thread
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
        
    def start(self) -> None:
        """Start the voice-input worker if it's not already started"""
        if self._thread is not None and self._thread.is_alive():
            return
        
        self._shutdown_event.clear()
        self._thread = Thread(
            target=self._target,
            args=(self._shutdown_event,),
            name="coda-voice-input",
            daemon=True,
        )
        self._thread.start()
        
    def stop(self, timeout: float = 4.0) -> None:
        """Signal the voice-input worker to stop and wait for it."""
        self._shutdown_event.set()
        
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            
    def is_alive(self) -> bool:
        """Return whether the voice-input thread is running."""
        return self._thread is not None and self._thread.is_alive()