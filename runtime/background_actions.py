"""Bounded, session-local ownership of background actions."""

from dataclasses import dataclass
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4


@dataclass
class _Job:
    stop: Event
    thread: Thread | None = None
    response: str | None = None
    metrics: dict | None = None


class BackgroundActions:
    def __init__(self):
        self._lock = Lock()
        self._jobs = {}
        self._closed = False

    def submit(self, work):
        with self._lock:
            if self._closed:
                raise ValueError("Background actions are shutting down.")
            finished = [key for key, job in self._jobs.items() if job.response is not None]
            if len(self._jobs) - len(finished) >= 4:
                raise ValueError("Four background actions are already running.")
            if len(self._jobs) >= 32:
                del self._jobs[finished[0]]
            key = uuid4().hex
            job = _Job(Event())
            job.thread = Thread(target=self._run, args=(job, work), daemon=True)
            self._jobs[key] = job
            try:
                job.thread.start()
            except RuntimeError:
                del self._jobs[key]
                raise
            return key

    def _run(self, job, work):
        def publish_metrics(metrics):
            with self._lock:
                job.metrics = metrics

        try:
            response = work(job.stop, publish_metrics)
        except Exception:
            response = "The background action could not complete."
        with self._lock:
            job.metrics = None
            job.response = response

    def inspect(self, key):
        with self._lock:
            job = self._jobs.get(key)
            return None if job is None or job.metrics is None else dict(job.metrics)

    def status(self, key):
        with self._lock:
            job = self._jobs.get(key)
            if job is None:
                return "No matching background action."
            return job.response or "The background action is still running."

    def cancel(self, key):
        with self._lock:
            job = self._jobs.get(key)
            if job is None or job.response is not None:
                return "No matching running background action."
            job.stop.set()
            return "Cancellation requested."

    def close(self, timeout):
        deadline = monotonic() + timeout
        with self._lock:
            self._closed = True
            jobs = tuple(self._jobs.values())
            for job in jobs:
                job.stop.set()
        for job in jobs:
            job.thread.join(max(0, deadline - monotonic()))
        return all(not job.thread.is_alive() for job in jobs)
