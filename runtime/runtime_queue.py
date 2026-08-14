from queue import Empty, Queue
from threading import Event, Lock
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


class SpeechTaskQueue(RuntimeQueue[SpeechTask]):
    """Track speech tasks until the speech worker finishes them."""

    def __init__(self):
        super().__init__()
        self._outstanding: dict[int, SpeechTask] = {}
        self._lock = Lock()

    def put(self, task: SpeechTask) -> None:
        task_key = id(task)

        with self._lock:
            self._outstanding[task_key] = task

            try:
                super().put(task)
            except Exception:
                self._outstanding.pop(task_key, None)
                raise

    def complete(self, task: SpeechTask) -> None:
        """Release cancellable ownership and mark a task as processed."""
        with self._lock:
            self._outstanding.pop(id(task), None)

        super().task_done()

    def cancel_current(self) -> Event | None:
        """Signal and return the oldest outstanding task's event."""
        with self._lock:
            task = next(iter(self._outstanding.values()), None)

            if task is None:
                return None

            task.cancel_event.set()
            return task.cancel_event

    def cancel_all(self) -> bool:
        """Signal every queued, resolving, or playing speech task."""
        with self._lock:
            tasks = tuple(self._outstanding.values())
            for task in tasks:
                task.cancel_event.set()

        return bool(tasks)
        

@dataclass(frozen=True)
class RuntimeQueues:
    """The queues shared by CODA's runtime workers."""
    
    requests: RuntimeQueue[RuntimeRequest] = field(
        default_factory=RuntimeQueue,
    )
    speech: SpeechTaskQueue = field(
        default_factory=SpeechTaskQueue,
    )
    events: RuntimeQueue[ExecutionResult| WorkerEvent] = field(
        default_factory=RuntimeQueue,
    )
