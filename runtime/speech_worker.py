from typing import Callable
from threading import Event, Thread
from runtime.runtime_queue import RuntimeQueue

from runtime.messages import (
    SpeechTask,
    WorkerEvent,
    WorkerEventType,
    WorkerName,
)
from runtime.speech_playback import (
    SpeechPlaybackController,
    SpeechProvider
)


ProviderResolver = Callable[[], list[SpeechProvider]]


class SpeechTaskProcessor:
    """Process speech tasks through the available TTS providers."""
    
    def __init__(
        self,
        resolve_providers: ProviderResolver,
        playback_controller: SpeechPlaybackController,
    ) -> None:
        self._resolve_providers = resolve_providers
        self._playback_controller = playback_controller
        
    def process(self, task: SpeechTask) -> WorkerEvent:
        if task.cancel_event.is_set():
            return self._event(task, WorkerEventType.CANCELLED)
        
        
        errors = []
        
        for provider in self._resolve_providers():
            if task.cancel_event.is_set():
                return self._event(task, WorkerEventType.CANCELLED)
            
            
            try:
                if not provider.is_available():
                    continue
                
                played = self._playback_controller.play(
                    provider,
                    task.text,
                    task.cancel_event,
                )
            except Exception as error:
                errors.append(f"{provider.name}: {error}")
                continue
            
            if task.cancel_event.is_set():
                return self._event(task, WorkerEventType.CANCELLED)
            
            if played:
                return self._event(task, WorkerEventType.COMPLETED)
            
            errors.append(f"{provider.name}: playback did not complete.")
            
        error_message = (
            "; ".join(errors)
            if errors
            else "No TTS provider is available."
        )
        
        return self._event(
            task,
            WorkerEventType.FAILED,
            error=error_message,
        )
    
    def stop(self) -> bool:
        """Stop the currently active playback session."""
        return self._playback_controller.stop()
    
    @staticmethod
    def _event(
        task: SpeechTask,
        event_type: WorkerEventType,
        error: str | None = None,
    ) -> WorkerEvent:
        return WorkerEvent(
            worker=WorkerName.SPEECH,
            event_type=event_type,
            request_id=task.request_id,
            error=error,
        )
        
        
class SpeechWorker:
    """Consume queued speech tasks on a dedicated thread."""

    def __init__(
        self,
        task_queue: RuntimeQueue[SpeechTask],
        event_queue: RuntimeQueue[WorkerEvent],
        processor: SpeechTaskProcessor,
        shutdown_event: Event,
    ) -> None:
        self._task_queue = task_queue
        self._event_queue = event_queue
        self._processor = processor
        self._shutdown_event = shutdown_event
        self._thread: Thread | None = None

    def start(self) -> None:
        if self.is_alive():
            return

        self._shutdown_event.clear()
        self._thread = Thread(
            target=self._run,
            name="coda-speech",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 4.0) -> None:
        self._shutdown_event.set()
        self._processor.stop()

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._shutdown_event.is_set():
            task = self._task_queue.get(timeout=0.1)

            if task is None:
                continue

            try:
                self._event_queue.put(
                    WorkerEvent(
                        worker=WorkerName.SPEECH,
                        event_type=WorkerEventType.STARTED,
                        request_id=task.request_id,
                    )
                )

                try:
                    result = self._processor.process(task)
                except Exception as error:
                    result = WorkerEvent(
                        worker=WorkerName.SPEECH,
                        event_type=WorkerEventType.FAILED,
                        request_id=task.request_id,
                        error=str(error),
                    )

                self._event_queue.put(result)
            finally:
                self._task_queue.task_done()