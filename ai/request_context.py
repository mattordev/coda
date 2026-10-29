"""Content-free request identity shared by nested provider operations."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event
from uuid import uuid4


@dataclass(frozen=True)
class RequestContext:
    trace_id: str
    cancel_event: Event | None = None


_current = ContextVar('coda_request_context', default=None)


def current_request():
    return _current.get()


def trace_id():
    current = current_request()
    return current.trace_id if current else str(uuid4())


def cancellation_event():
    current = current_request()
    return current.cancel_event if current else None


@contextmanager
def request_context(request_id=None, cancel_event=None):
    """Nested callers inherit identity; finally always restores the caller."""
    previous = current_request()
    context = RequestContext(
        request_id or (previous.trace_id if previous else str(uuid4())),
        cancel_event if cancel_event is not None else (previous.cancel_event if previous else None),
    )
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
