from threading import Event, Lock
from typing import Protocol



class SpeechSession(Protocol):
    """One provider-specific speech playback session."""

    def play(self, cancel_event: Event) -> bool:
        """Play speech until complete or cancelled."""
        ...

    def stop(self) -> None:
        """Stop this session's active playback."""
        ...


class SpeechProvider(Protocol):
    """Create speech sessions without exposing provider details"""
    
    name: str
    
    def is_available(self) -> bool:
        """Return whether this provider can currently be used."""
        ...
        
    def create_session(self, text: str) -> SpeechSession:
        """Prepare one playback session for the supplied text"""
        ...
        

class SpeechPlaybackController:
    """Own and safely interrupt the currently active speech session"""
    
    def __init__(self) -> None:
        self._lock = Lock()
        self._active_session: SpeechSession | None = None
        self._active_cancel_event: Event | None = None
        
    def play(self, provider:SpeechProvider, text: str, cancel_event: Event) -> bool:
        if cancel_event.is_set():
            return False
        
        
        session = provider.create_session(text)
        
        with self._lock:
            if self._active_session is not None:
                raise RuntimeError("Speech playback is already active.")
            
            if cancel_event.is_set():
                return False
            
            self._active_session = session
            self._active_cancel_event = cancel_event
            
        try:
            return session.play(cancel_event)
        finally:
            with self._lock:
                if self._active_session is session:
                    self._active_session = None
                    self._active_cancel_event = None
                    
    def stop(self, expected_cancel_event: Event | None = None) -> bool:
        with self._lock:
            session = self._active_session
            cancel_event = self._active_cancel_event

            if (
                expected_cancel_event is not None
                and cancel_event is not expected_cancel_event
            ):
                return False

        if session is None:
            return False

        if cancel_event is not None:
            cancel_event.set()
        
        session.stop()
        return True
