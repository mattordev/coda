"""Brief feedback while a voice request is working, separate from its answer."""

import random
from threading import Event, Lock, Thread
import time

from runtime.messages import InputSource, SpeechTask
from ai.tools.progress import ToolFeedback


PHRASES = (
    "Sure, one moment.",
    "Okay, give me a moment.",
    "Sure, let me take a look.",
)

ACTION_MESSAGES = {
    'search_web': "I'm searching the web now.",
    'read_url': "I'm reading the webpage now.",
    'list_directory': "I'm checking the folder now.",
    'read_file': "I'm reading the file now.",
    'get_file_metadata': "I'm checking the file details now.",
    'search_files': "I'm searching the filenames now.",
    'search_content': "I'm searching inside the files now.",
    'create_file': "I'm creating the file now.",
    'create_directory': "I'm creating the folder now.",
    'edit_file': "I'm editing the file now.",
    'append_file': "I'm adding the text to the file now.",
    'copy_path': "I'm copying it now.",
    'move_path': "I'm moving it now.",
    'delete_path': "I'm deleting it now.",
    'time': "I'm checking the time.",
    'status': "I'm checking the status.",
    'connected': "I'm checking the connection.",
}


class VoiceAcknowledgement:
    """Queue at most one filler; retire it before any real answer is queued.

    Its cancellation event is separate: stopping filler must never cancel the
    user's request. The watcher also propagates request cancellation/shutdown.
    """

    def __init__(self, request, queue, stop_playback, shutdown, *, delay=0.8,
                 choose=random.choice, clock=time.monotonic):
        self._request = request
        self._queue = queue
        self._stop_playback = stop_playback
        self._shutdown = shutdown
        self._delay = delay
        self._choose = choose
        self._clock = clock
        self._cancel = Event()
        self._lock = Lock()
        self._queued = False
        self._finished = False
        self._fallback_reported = False
        self._action_tool = None
        self._last_completed = None
        self._search_count = None
        self._progress_count = 0
        self._progress_seen = set()
        self._thread = None

    def start(self):
        if self._request.source != InputSource.VOICE:
            return
        self._thread = Thread(target=self._run, name="coda-acknowledgement", daemon=True)
        self._thread.start()

    def finish(self):
        """Suppress queued filler and interrupt only its own playback."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._cancel.set()
            queued = self._queued
        if queued:
            self._stop_playback(expected_cancel_event=self._cancel)

    def report(self, event):
        """Speak bounded, factual lifecycle updates, never raw tool content."""
        if self._request.source != InputSource.VOICE:
            return
        with self._lock:
            if (self._finished or self._cancel.is_set() or self._request.cancel_event.is_set()
                    or self._shutdown.is_set()):
                return
            if isinstance(event, ToolFeedback):
                text = self._tool_message(event)
                if text is None:
                    return
            elif event == 'multiple_provider_failures' and not self._fallback_reported:
                self._fallback_reported = True
                text = "A couple of AI providers haven't worked. I'm trying another option."
            else:
                return
            self._queue.put(SpeechTask(
                request_id=self._request.request_id, text=text,
                cancel_event=self._cancel, is_acknowledgement=True,
            ))
            # An early failure update also replaces the initial filler.
            self._queued = True

    def _tool_message(self, event):
        tool = event.tool.rsplit('.', 1)[-1]
        if event.stage == 'completed':
            self._last_completed = tool
            if tool == 'search_web' and type(event.count) is int and 0 <= event.count <= 10:
                self._search_count = event.count
            return None
        if event.stage == 'action':
            if self._action_tool is None:
                self._action_tool = tool
                return ACTION_MESSAGES.get(tool, "I'm running that tool now.")
            if tool == self._action_tool:
                return None
            text = ACTION_MESSAGES.get(tool, "I'm moving on to the next tool.")
            if tool == 'read_url' and self._search_count:
                text = "I've found search results. I'm reading the selected pages now."
        elif event.stage == 'progress':
            if self._last_completed == 'search_web' and self._search_count:
                text = "I've found search results. I'm checking their snippets."
            elif self._last_completed == 'read_url':
                text = "I've retrieved the page text. I'm working on the summary."
            elif self._last_completed == 'read_file':
                text = "I've read the file. I'm working through its contents."
            else:
                text = "I've got the tool results. I'm working through them."
        else:
            return None
        if text in self._progress_seen or self._progress_count >= 2:
            return None
        self._progress_seen.add(text)
        self._progress_count += 1
        return text

    def _run(self):
        deadline = self._clock() + self._delay
        try:
            while not self._cancel.is_set():
                if self._request.cancel_event.is_set() or self._shutdown.is_set():
                    return
                with self._lock:
                    if not self._cancel.is_set() and not self._queued and self._clock() >= deadline:
                        self._queue.put(SpeechTask(
                            request_id=self._request.request_id,
                            text=self._choose(PHRASES), cancel_event=self._cancel,
                            is_acknowledgement=True,
                        ))
                        self._queued = True
                self._cancel.wait(0.05)
        finally:
            self.finish()
