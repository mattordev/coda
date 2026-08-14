"""Capture microphone phrases together with state from their start.

The VAD loop is adapted from SpeechRecognition 3.17.0's ``_listen`` method,
copyright Anthony Zhang (Uberi) and distributed under the BSD-3-Clause
license. CODA keeps the adaptation local because the upstream API does not
expose the accepted phrase-start boundary.
"""

from collections import deque
from dataclasses import dataclass
import math
from threading import Event
from typing import Callable, Generic, TypeVar

import audioop

import speech_recognition as sr


CaptureState = TypeVar("CaptureState")


@dataclass(frozen=True)
class CapturedPhrase(Generic[CaptureState]):
    """Audio plus application state captured when its phrase began."""

    audio: sr.AudioData
    started_with: CaptureState
    overlapped_capture: bool = False


def listen_for_phrase(
    recognizer: sr.Recognizer,
    source: sr.AudioSource,
    capture_state: Callable[[], CaptureState],
    *,
    timeout: float | None = None,
    phrase_time_limit: float | None = None,
    stop_event: Event | None = None,
    blocks_capture: Callable[[CaptureState], bool] | None = None,
) -> CapturedPhrase[CaptureState] | None:
    """Listen using SpeechRecognition's VAD and retain accepted start state.

    SpeechRecognition exposes neither the start time of an accepted phrase nor
    a callback at that boundary. This mirrors its non-streaming VAD loop so the
    state snapshot can be replaced whenever a short noise candidate is rejected.
    """
    if source.stream is None:
        raise ValueError("The audio source must be entered before listening.")

    if recognizer.pause_threshold < recognizer.non_speaking_duration:
        raise ValueError(
            "pause_threshold must be at least non_speaking_duration."
        )

    seconds_per_buffer = float(source.CHUNK) / source.SAMPLE_RATE
    pause_buffer_count = math.ceil(
        recognizer.pause_threshold / seconds_per_buffer
    )
    phrase_buffer_count = math.ceil(
        recognizer.phrase_threshold / seconds_per_buffer
    )
    non_speaking_buffer_count = math.ceil(
        recognizer.non_speaking_duration / seconds_per_buffer
    )

    elapsed_time = 0.0
    buffer = b""

    while True:
        frames = deque()
        phrase_state = None
        overlapped_capture = False

        while True:
            if stop_event is not None and stop_event.is_set():
                return None

            elapsed_time += seconds_per_buffer
            if timeout and elapsed_time > timeout:
                raise sr.WaitTimeoutError(
                    "listening timed out while waiting for phrase to start"
                )

            buffer_state = capture_state()
            buffer = source.stream.read(source.CHUNK)
            post_read_state = capture_state()
            if blocks_capture is not None:
                overlapped_capture = (
                    overlapped_capture or blocks_capture(post_read_state)
                )
            if not buffer:
                break

            frames.append(buffer)
            if len(frames) > non_speaking_buffer_count:
                frames.popleft()

            energy = audioop.rms(buffer, source.SAMPLE_WIDTH)
            if energy > recognizer.energy_threshold:
                phrase_state = buffer_state
                if blocks_capture is not None:
                    overlapped_capture = (
                        overlapped_capture or blocks_capture(buffer_state)
                    )
                break

            if recognizer.dynamic_energy_threshold:
                damping = (
                    recognizer.dynamic_energy_adjustment_damping
                    ** seconds_per_buffer
                )
                target_energy = energy * recognizer.dynamic_energy_ratio
                recognizer.energy_threshold = (
                    recognizer.energy_threshold * damping
                    + target_energy * (1 - damping)
                )

        pause_count = 0
        phrase_count = 0
        phrase_start_time = elapsed_time

        while buffer:
            if stop_event is not None and stop_event.is_set():
                return None

            elapsed_time += seconds_per_buffer
            if (
                phrase_time_limit
                and elapsed_time - phrase_start_time > phrase_time_limit
            ):
                break

            buffer_state = capture_state()
            if blocks_capture is not None:
                overlapped_capture = (
                    overlapped_capture or blocks_capture(buffer_state)
                )

            buffer = source.stream.read(source.CHUNK)
            post_read_state = capture_state()
            if blocks_capture is not None:
                overlapped_capture = (
                    overlapped_capture or blocks_capture(post_read_state)
                )
            if not buffer:
                break

            frames.append(buffer)
            phrase_count += 1

            energy = audioop.rms(buffer, source.SAMPLE_WIDTH)
            if energy > recognizer.energy_threshold:
                pause_count = 0
            else:
                pause_count += 1

            if pause_count > pause_buffer_count:
                break

            if recognizer.dynamic_energy_threshold:
                damping = (
                    recognizer.dynamic_energy_adjustment_damping
                    ** seconds_per_buffer
                )
                target_energy = energy * recognizer.dynamic_energy_ratio
                recognizer.energy_threshold = (
                    recognizer.energy_threshold * damping
                    + target_energy * (1 - damping)
                )

        phrase_count -= pause_count
        if phrase_count >= phrase_buffer_count or not buffer:
            break

    for _ in range(pause_count - non_speaking_buffer_count):
        frames.pop()

    if phrase_state is None:
        phrase_state = capture_state()

    audio = sr.AudioData(
        b"".join(frames),
        source.SAMPLE_RATE,
        source.SAMPLE_WIDTH,
    )
    return CapturedPhrase(
        audio=audio,
        started_with=phrase_state,
        overlapped_capture=overlapped_capture,
    )
