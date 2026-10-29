"""Optional background model preparation with bounded retirement and safe status."""

from copy import deepcopy
import os
import sys
from threading import Event, Lock, Thread, current_thread
import time

import psutil
from utils.console import print_line as print
from utils.model_activity import ModelActivity


def enabled(subsystem, argv=None):
    """Explicit CLI choice wins over the effective environment; default disabled."""
    positive, negative = f'--prewarm-{subsystem}', f'--no-prewarm-{subsystem}'
    choices = [argument == positive for argument in (sys.argv[1:] if argv is None else argv)
               if argument in (positive, negative)]
    if choices:
        return choices[-1]
    return os.getenv(f'CODA_PREWARM_{subsystem.upper()}', '').strip().lower() in ('1', 'true', 'yes', 'on')


def memory_snapshot():
    """RSS belongs to this process; external LLM servers cannot be measured here."""
    rss, vram = psutil.Process().memory_info().rss, None
    torch = sys.modules.get('torch')
    if torch is not None and torch.cuda.is_initialized():
        vram = torch.cuda.memory_allocated()
    return {'rss_bytes': rss, 'vram_bytes': vram}


def resources_available():
    try:
        minimum = int(os.getenv('CODA_PREWARM_MIN_AVAILABLE_MB', '1024'))
        if minimum < 0:
            return False
        return psutil.virtual_memory().available >= minimum * 1024 * 1024
    except (ValueError, OSError):
        return False


class ModelWarmup:
    """A generation owns its callbacks; closing cannot publish stale readiness."""

    def __init__(self, sample=None, eligible=None):
        self._lock = Lock()
        self._stop = Event()
        self._tasks = {}
        self._threads = []
        self._sample = sample or memory_snapshot
        self._eligible = eligible or resources_available

    def start(self, tasks):
        """Tasks map names to a callback(cancel_event), or None for skipped tasks."""
        with self._lock:
            if self._stop.is_set() or self._tasks:
                return
            pending = []
            for name, callback in tasks.items():
                done = Event()
                if callback is None:
                    done.set()
                self._tasks[name] = {'state': 'skipped' if callback is None else 'queued',
                                     'duration_seconds': None, 'memory_before': None,
                                     'memory_after': None, 'error': None, 'done': done}
                if callback is not None:
                    pending.append((name, callback))
            if pending:
                self._threads.append(Thread(target=self._run, args=(pending,),
                                            name='model-warmup', daemon=True))
            workers = tuple(self._threads)
        for worker in workers:
            worker.start()

    def _measure(self):
        try:
            return self._sample()
        except Exception:
            return {'rss_bytes': None, 'vram_bytes': None}

    def _run(self, pending):
        # Serial background loading limits transient RAM/VRAM pressure. Ordinary
        # requests remain free to use unrelated providers. The intent LLM goes
        # first so its first request never waits behind a speech-model download.
        for name, callback in pending:
            self._prepare(name, callback)

    def _prepare(self, name, callback):
        started, before = time.monotonic(), self._measure()
        state, error = 'ready', None
        try:
            if self._stop.is_set() or not self._eligible():
                state = 'skipped'
            else:
                with self._lock:
                    self._tasks[name]['state'] = 'warming'
                with ModelActivity(f'{name.upper()} prewarm', 'warming', cancel_event=self._stop) as activity:
                    if callback(self._stop) is False:
                        state = 'skipped'
                        activity.finish('skipped')
        except Exception:
            state, error = 'failed', 'Model preparation failed; normal provider fallback remains available.'
        finally:
            after = self._measure()
            with self._lock:
                task = self._tasks[name]
                task.update(state='skipped' if self._stop.is_set() else state,
                            duration_seconds=time.monotonic() - started,
                            memory_before=before, memory_after=after, error=error)
                task['done'].set()
            if state == 'failed' and not self._stop.is_set():
                print(f'[PREWARM] {name}: {error}', flush=True)

    def wait(self, name, cancel_event=None):
        """Only this provider's first use waits; unrelated work is unaffected."""
        with self._lock:
            task = self._tasks.get(name)
            done = task['done'] if task else None
        while done is not None and not done.wait(0.05):
            if self._stop.is_set() or (cancel_event is not None and cancel_event.is_set()):
                return False
        return cancel_event is None or not cancel_event.is_set()

    def snapshot(self):
        with self._lock:
            return {name: deepcopy({key: value for key, value in task.items() if key != 'done'})
                    for name, task in self._tasks.items()}

    def close(self, timeout_seconds=4):
        self._stop.set()
        deadline = time.monotonic() + timeout_seconds
        for worker in self._threads:
            if worker is not current_thread() and worker.ident is not None:
                worker.join(max(0, deadline - time.monotonic()))
        return all(not worker.is_alive() for worker in self._threads)


_lifecycle_lock = Lock()
_active = None


def start(argv=None):
    """Build only eligible local tasks after normal environment/config loading."""
    global _active
    from ai.intents.factory import _get_local_classifier_provider, _local_classifier_enabled
    import utils.speak_response as speech
    import utils.stt_service as stt

    tasks = {'llm': None, 'tts': None, 'stt': None}
    llm_provider = None
    if enabled('llm', argv) and _local_classifier_enabled():
        selection = _get_local_classifier_provider()
        if selection is not None:
            llm_provider, provider = selection
            warm = getattr(provider, 'warmup', None)
            if callable(warm):
                tasks['llm'] = warm
    if enabled('tts', argv):
        tasks['tts'] = speech.prewarm
    if enabled('stt', argv) and stt.get_stt_provider() == 'faster-whisper':
        tasks['stt'] = stt.prewarm
    with _lifecycle_lock:
        if _active is None:
            _active = ModelWarmup()
            _active.llm_provider = llm_provider
            _active.start(tasks)
        return _active


def shutdown(timeout_seconds=4):
    global _active
    with _lifecycle_lock:
        previous, _active = _active, None
    return previous is None or previous.close(timeout_seconds)


def snapshot():
    with _lifecycle_lock:
        active = _active
    return {} if active is None else active.snapshot()


def wait_for_llm(provider_name, cancel_event=None):
    with _lifecycle_lock:
        active = _active
    return active is None or active.llm_provider != provider_name or active.wait('llm', cancel_event)
