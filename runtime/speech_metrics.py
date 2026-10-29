"""Content-free, bounded request-to-audio measurements using a monotonic clock."""
from collections import deque
import json
from pathlib import Path
from threading import Lock
import time

_LOCK = Lock()
_RECORDS = deque(maxlen=1000)
_PATH = Path(__file__).resolve().parents[1] / 'logs' / 'speech' / 'active-session.jsonl'
_STAGES = ('first_visible', 'first_chunk', 'first_pcm', 'playback_start', 'generation_complete')


def record_metrics(record):
    """Persist only stage durations, identity and outcome, never answer text."""
    with _LOCK:
        _RECORDS.append(record)
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = _PATH.with_suffix('.tmp')
            temporary.write_text(''.join(json.dumps(item) + '\n' for item in _RECORDS), encoding='utf-8')
            temporary.replace(_PATH)
        except OSError:
            # Optional telemetry cannot break speech or cancellation.
            pass


class SpeechMetrics:
    def __init__(self, request_id, accepted_at, *, mode='full_answer', clock=time.perf_counter,
                 record=record_metrics):
        self.request_id = request_id
        self.accepted_at = accepted_at
        self.mode = mode
        self.clock = clock
        self.record = record
        self._lock = Lock()
        self._stages = {}
        self._finished = False

    def mark(self, stage):
        if stage not in _STAGES:
            raise ValueError('Unknown speech stage.')
        with self._lock:
            if not self._finished:
                self._stages.setdefault(stage, max(0.0, self.clock() - self.accepted_at))

    def snapshot(self, outcome=None):
        with self._lock:
            return self._snapshot(outcome)

    def _snapshot(self, outcome):
        stages = {name + '_seconds': self._stages.get(name) for name in _STAGES}
        return dict(schema_version=1, request_id=self.request_id, mode=self.mode,
                    **stages, first_audio_seconds=stages['playback_start_seconds'],
                    audio_measurement='first_successful_pcm_pipe_write',
                    completion_seconds=self._stages.get('completion'), outcome=outcome)

    def finish(self, outcome):
        with self._lock:
            if self._finished:
                return
            self._stages['completion'] = max(0.0, self.clock() - self.accepted_at)
            self._finished = True
            record = self._snapshot(outcome)
        self.record(record)
