"""Explicit synthetic or private-LAN LLM -> PocketTTS latency experiment.

No cloud calls, tool actions or private prompts. Synthetic PCM is not audio
quality evidence. Real playback timing is a pipe-write proxy, not loopback.
"""
import argparse
from contextlib import nullcontext
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from threading import Event, Thread
import time
from unittest.mock import patch

import requests

from ai.providers import llamacpp
from ai.providers.cancellable import CancellationScope, run_cancellable
from ai.providers.generation import GenerationOptions
from ai.providers.telemetry import ProviderMetricsCollector
from benchmarks.llm_workloads import local_url, ProbeHTTP
from runtime.incremental_speech import IncrementalSpeech
from runtime.messages import SpeechTask
from runtime.speech_metrics import SpeechMetrics
from runtime.speech_playback import SpeechPlaybackController
from runtime.speech_worker import SpeechTaskProcessor
from tts.pockettts_provider import PocketTTSProvider

PROMPT = ('Explain in six short sentences how a local voice assistant processes a request, '
          'from microphone input to spoken output. Use plain prose and no headings.')
SYNTHETIC_TEXT = ('A local assistant listens for a request. It converts the captured speech into text. '
                  'The router selects an appropriate operation. A model produces a useful answer. '
                  'Speech synthesis creates audio from that answer. Playback speaks it to the user.')


class SyntheticPCM:
    sample_rate = 24000

    def stream_pcm(self, text):
        for _ in range(2):
            time.sleep(.005)
            yield b'\0' * 960


class SyntheticPlayer:
    """A bounded immediate PCM sink; never claims real speaker latency."""
    def __init__(self, *args, **kwargs):
        self.returncode = None
        self.stdin = self

    def write(self, chunk):
        return len(chunk)

    def flush(self):
        pass

    def close(self):
        self.returncode = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def synthetic_generation(callback, cancel):
    for word in SYNTHETIC_TEXT.split(' '):
        if cancel.is_set():
            return None, 'Request cancelled.', {}
        time.sleep(.01)
        callback(word + ' ')
    return SYNTHETIC_TEXT, None, {}


def local_generation(base_url, model, timeout):
    def generate(callback, cancel):
        collector = ProviderMetricsCollector()
        scope = CancellationScope()
        row = {}
        with requests.Session() as session:
            scope.add(session.close)
            try:
                answer, error = run_cancellable(lambda: llamacpp._generate_stream(
                    ProbeHTTP(session, row), base_url, model,
                    [{'role': 'system', 'content': 'Give concise factual plain-text answers.'},
                     {'role': 'user', 'content': PROMPT}], timeout, cancel, scope, collector,
                    GenerationOptions(thinking=False, max_tokens=512, on_visible_text=callback),
                ), cancel, timeout, 'Benchmark generation timed out.', on_abandon=scope.cancel)
                result = collector.finish(response=answer, error=error)
                return answer, error, dict(asdict(result.metrics), **row)
            finally:
                scope.cancel()
    return generate


def run_trial(mode, generate, provider):
    cancel = Event()
    records, events, threads = [], [], []
    metrics = SpeechMetrics('benchmark', time.perf_counter(), mode=mode, record=records.append)
    processor = SpeechTaskProcessor(lambda: [provider], SpeechPlaybackController())
    def start(stream):
        task = SpeechTask('benchmark', '', cancel, stream=stream, metrics=metrics)
        worker = Thread(target=lambda: events.append(processor.process(task)), daemon=True)
        threads.append(worker)
        worker.start()
    stream = IncrementalSpeech('benchmark', cancel, start, metrics=metrics)
    def visible(text):
        metrics.mark('first_visible')
        if mode == 'incremental':
            stream.feed(text)
    try:
        answer, error, provider_metrics = generate(visible, cancel)
        metrics.mark('generation_complete')
        if error or not answer:
            stream.abort()
            metrics.finish('failed' if error else 'empty')
        elif mode == 'incremental':
            stream.finish()
        else:
            events.append(processor.process(SpeechTask('benchmark', answer, cancel, metrics=metrics)))
        for worker in threads:
            worker.join(180)
            if worker.is_alive():
                cancel.set()
                raise TimeoutError('Speech benchmark did not finish.')
        result = records[-1] if records else metrics.snapshot('failed')
        return dict(result, provider_metrics=provider_metrics, error=error,
                    semantic_chars=len(answer or ''),
                    semantic_sha256=hashlib.sha256((answer or '').encode()).hexdigest(),
                    audio_before_generation_complete=(
                        result['first_audio_seconds'] is not None
                        and result['generation_complete_seconds'] is not None
                        and result['first_audio_seconds'] < result['generation_complete_seconds']))
    finally:
        cancel.set()
        stream.abort()
        processor.stop(expected_cancel_event=cancel)
        for worker in threads:
            worker.join(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-local', action='store_true')
    parser.add_argument('--base-url', type=local_url)
    parser.add_argument('--model')
    parser.add_argument('--tts', choices=['synthetic', 'pockettts'], default='synthetic')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout <= 0:
        parser.error('repeat and timeout must be positive')
    if args.run_local and (not args.base_url or not args.model):
        parser.error('--run-local requires --base-url and --model')
    generate = local_generation(args.base_url, args.model, args.timeout) if args.run_local else synthetic_generation
    provider = (PocketTTSProvider() if args.tts == 'pockettts' else
                PocketTTSProvider(runtime_factory=lambda *_: SyntheticPCM(), ffplay_path='synthetic'))
    if not provider.is_available():
        raise RuntimeError('Selected PocketTTS runtime/player is unavailable.')
    rows = []
    guard = (patch('tts.pockettts_provider.Popen', SyntheticPlayer)
             if args.tts == 'synthetic' else nullcontext())
    try:
        with guard:
            for repeat in range(args.repeat):
                order = ('full_answer', 'incremental') if repeat % 2 == 0 else ('incremental', 'full_answer')
                for mode in order:
                    row = run_trial(mode, generate, provider)
                    row['repeat'] = repeat + 1
                    rows.append(row)
                    print(json.dumps(row), flush=True)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(dict(
                        llm='local' if args.run_local else 'synthetic', model=args.model,
                        tts=args.tts, tts_warm=True, thinking=False, max_tokens=512,
                        seed=42, temperature=1, cache_prompt_requested=False,
                        measurement='first successful PCM pipe write; no acoustic loopback', rows=rows,
                    ), indent=2), encoding='utf-8')
    finally:
        provider.close()


if __name__ == '__main__':
    main()
