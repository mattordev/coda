from threading import Lock
import time

class FollowUpState:
    """Manage the shared voice follow-up window safely."""
    
    def __init__(self):
        self._active_until = 0.0
        self._lock = Lock()
        
    def open(self, duration_seconds: float) -> None:
        """Open the follow-up window for the requested duration."""
        with self._lock:
            self._active_until = time.monotonic() + duration_seconds
        
    def close(self) -> None:
        """Close the current follow-up window."""
        with self._lock:
            self._active_until = 0.0
            
    def is_active(self) -> bool:
        """Return whether the follow-up window is still active."""
        with self._lock:
            return time.monotonic() < self._active_until