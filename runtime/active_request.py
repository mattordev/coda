from threading import Lock

from runtime.messages import RuntimeRequest

class ActiveRequestState:
    """Track and cancel the request currently being executed."""
    
    def __init__(self):
        self._request: RuntimeRequest | None = None
        self._lock = Lock()
        
    def activate(self, request: RuntimeRequest) -> None:
        with self._lock:
            self._request = request
            
    def cancel_active(self) -> str | None:
        with self._lock:
            if self._request is None:
                return None
            
            self._request.cancel_event.set()
            return self._request.request_id
        
    def clear(self, request_id: str) -> bool:
        with self._lock:
            if (
                self._request is None
                or self._request.request_id != request_id
            ):
                return False
            
            self._request = None
            return True
        
    def active_request_id(self) -> str | None:
        with self._lock:
            if self._request is None:
                return None
            
            return self._request.request_id

    def active_request(self) -> RuntimeRequest | None:
        """Return the request currently owned by the execution worker."""
        with self._lock:
            return self._request
