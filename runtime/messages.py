from dataclasses import dataclass, field
from enum import Enum
from threading import Event
import time
import uuid

class InputSource(str, Enum):
    MANUAL = "manual"
    VOICE = "voice"

@dataclass(frozen=True)
class RuntimeRequest:
    """A user request submitted to the runtime."""
    message: str
    source: InputSource
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex) # generates the req id using uuid hex
    created_at: float = field(default_factory=time.time)
    cancel_event: Event = field(
        default_factory=Event,
        repr=False,
        compare=False,
    )
    execution_complete: Event = field(
        default_factory=Event,
        repr=False,
        compare=False,
    )
    replace_active: bool = False
    
@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of processing a runtime req."""
    
    request_id: str
    handled: bool
    response_text: str | None = None
    open_follow_up: bool = False
    cancelled: bool = False
    error: str | None = None
    
@dataclass(frozen=True)
class SpeechTask:
    """Text queued for speech on behalf of a runtime req."""
    
    request_id: str
    text: str
    cancel_event: Event = field(
        repr=False,
        compare=False,
    )
    open_follow_up: bool = False
    
class WorkerName(str, Enum):
    VOICE_INPUT = "voice_input"
    EXECUTION = "execution"
    SPEECH = "speech"
    
class WorkerEventType(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    
@dataclass(frozen=True)
class WorkerEvent:
    """A lifecycle event published by a runtime worker."""
    
    worker: WorkerName
    event_type: WorkerEventType
    request_id: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    open_follow_up: bool = False
