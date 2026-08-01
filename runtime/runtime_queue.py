from queue import Empty, Queue
from typing import Generic, TypeVar

from dataclasses import dataclass, field

from runtime.messages import (
    ExecutionResult,
    RuntimeRequest,
    SpeechTask,
    WorkerEvent,
)

MessageType = TypeVar("MessageType")

class RuntimeQueue(Generic[MessageType]):
    """A thread-safe queue for runtime messages"""
    
    def __init__(self):
        self._queue: Queue[MessageType] = Queue()
        
    def put(self, message: MessageType) -> None:
        """Add a message to the queue"""
        self._queue.put(message)
        
    def get(self, timeout: float | None = None) -> MessageType | None:
        """wait for and return a message, or return None type after a timeout"""
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None
        
    def task_done(self) -> None:
        """Mark the retrieved message as processed."""
        self._queue.task_done()
        
    def join(self) -> None:
        """Wait untill every message in the queue has been processed."""
        self._queue.join()
        

@dataclass(frozen=True)
class RuntimeQueues:
    """The queues shared by CODA's runtime workers."""
    
    requests: RuntimeQueue[RuntimeRequest] = field(
        default_factory=RuntimeQueue,
    )
    speech: RuntimeQueue[SpeechTask] = field(
        default_factory=RuntimeQueue,
    )
    events: RuntimeQueue[ExecutionResult| WorkerEvent] = field(
        default_factory=RuntimeQueue,
    )