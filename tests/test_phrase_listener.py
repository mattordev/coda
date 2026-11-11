import struct
import unittest
from collections import deque
from threading import Event

import speech_recognition as sr

from utils.phrase_listener import listen_for_phrase


def _pcm_buffer(amplitude, sample_count=10):
    return struct.pack("<h", amplitude) * sample_count


class FakeAudioStream:
    def __init__(self, buffers, before_read=None):
        self._buffers = deque(buffers)
        self._before_read = before_read
        self.read_count = 0

    def read(self, _chunk_size):
        self.read_count += 1
        if self._before_read is not None:
            self._before_read(self.read_count)
        return self._buffers.popleft() if self._buffers else b""


class FakeAudioSource:
    CHUNK = 10
    SAMPLE_RATE = 100
    SAMPLE_WIDTH = 2

    def __init__(self, buffers, before_read=None):
        self.stream = FakeAudioStream(
            buffers,
            before_read=before_read,
        )


def _recognizer():
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 100
    recognizer.dynamic_energy_threshold = False
    recognizer.pause_threshold = 0.2
    recognizer.phrase_threshold = 0.2
    recognizer.non_speaking_duration = 0.1
    return recognizer


class PhraseListenerTests(unittest.TestCase):
    def test_playback_end_releases_echo_without_waiting_for_silence(self):
        speech, quiet = _pcm_buffer(400), _pcm_buffer(0)
        playing = True
        def end_playback(read_count):
            nonlocal playing
            if read_count == 3:
                playing = False
        source = FakeAudioSource(
            [speech, speech, quiet, speech, speech, speech, quiet, quiet, quiet],
            before_read=end_playback,
        )
        recognizer = _recognizer()
        recognizer.pause_threshold = 1.8
        echo = listen_for_phrase(recognizer, source, lambda: playing,
                                 blocks_capture=bool, end_on_playback_end=True)
        self.assertTrue(echo.overlapped_capture)
        self.assertTrue(echo.playback_ended)
        self.assertEqual(source.stream.read_count, 3)
        # Starting a fresh capture must not consume the next user's onset as echo.
        recognizer.pause_threshold = 0.2
        fresh = listen_for_phrase(recognizer, source, lambda: playing,
                                  blocks_capture=bool, end_on_playback_end=True)
        self.assertFalse(fresh.overlapped_capture)
        self.assertFalse(fresh.playback_ended)
        self.assertEqual(fresh.audio.frame_data, speech * 3 + quiet)

    def test_waiting_callback_runs_during_idle_reads_not_phrase_capture(self):
        quiet, speech = _pcm_buffer(0), _pcm_buffer(400)
        source = FakeAudioSource([quiet, speech, speech, speech, quiet, quiet, quiet])
        waiting = []
        captured = listen_for_phrase(
            _recognizer(), source, lambda: source.stream.read_count,
            on_waiting=waiting.append,
        )
        self.assertEqual(waiting, [0, 1])
        self.assertIsNotNone(captured)

    def test_captures_state_at_energy_onset_after_waiting(self):
        quiet = _pcm_buffer(0)
        speech = _pcm_buffer(400)
        follow_up_active = False

        def update_state(read_count):
            nonlocal follow_up_active
            if read_count == 1:
                follow_up_active = True

        source = FakeAudioSource(
            [quiet, speech, speech, speech, quiet, quiet, quiet],
            before_read=update_state,
        )

        captured = listen_for_phrase(
            _recognizer(),
            source,
            lambda: follow_up_active,
        )

        self.assertIsNotNone(captured)
        self.assertTrue(captured.started_with)

    def test_energy_buffer_uses_state_from_before_blocking_read(self):
        quiet = _pcm_buffer(0)
        speech = _pcm_buffer(400)
        speech_playing = True

        def end_playback_during_read(read_count):
            nonlocal speech_playing
            if read_count == 1:
                speech_playing = False

        source = FakeAudioSource(
            [speech, speech, speech, quiet, quiet, quiet],
            before_read=end_playback_during_read,
        )

        captured = listen_for_phrase(
            _recognizer(),
            source,
            lambda: speech_playing,
        )

        self.assertIsNotNone(captured)
        self.assertTrue(captured.started_with)
        self.assertFalse(speech_playing)

    def test_discards_state_from_too_short_noise_candidate(self):
        quiet = _pcm_buffer(0)
        noise = _pcm_buffer(200)
        speech = _pcm_buffer(400)
        captured_states = []
        current_state = "noise"

        def capture_state():
            captured_states.append(current_state)
            return current_state

        def replace_state_after_discarded_noise(read_count):
            nonlocal current_state
            if read_count == 5:
                current_state = "accepted"

        source = FakeAudioSource(
            [
                noise,
                quiet,
                quiet,
                quiet,
                quiet,
                speech,
                speech,
                speech,
                quiet,
                quiet,
                quiet,
            ],
            before_read=replace_state_after_discarded_noise,
        )

        captured = listen_for_phrase(
            _recognizer(),
            source,
            capture_state,
        )

        self.assertIsNotNone(captured)
        self.assertEqual(captured_states[0], "noise")
        self.assertEqual(captured_states[-1], "accepted")
        self.assertIn("accepted", captured_states)
        self.assertEqual(captured.started_with, "accepted")
        self.assertEqual(
            captured.audio.frame_data,
            speech + speech + speech + quiet,
        )

    def test_records_playback_that_starts_after_phrase_onset(self):
        speech = _pcm_buffer(400)
        quiet = _pcm_buffer(0)
        speech_playing = False

        def start_playback_mid_capture(read_count):
            nonlocal speech_playing
            if read_count == 2:
                speech_playing = True

        source = FakeAudioSource(
            [speech, speech, speech, quiet, quiet, quiet],
            before_read=start_playback_mid_capture,
        )

        captured = listen_for_phrase(
            _recognizer(),
            source,
            lambda: speech_playing,
            blocks_capture=lambda state: state,
        )

        self.assertIsNotNone(captured)
        self.assertFalse(captured.started_with)
        self.assertTrue(captured.overlapped_capture)

    def test_records_playback_starting_during_final_capture_read(self):
        speech = _pcm_buffer(400)
        quiet = _pcm_buffer(0)
        speech_playing = False

        def start_playback_in_final_read(read_count):
            nonlocal speech_playing
            if read_count == 4:
                speech_playing = True

        source = FakeAudioSource(
            [speech, speech, speech, b""],
            before_read=start_playback_in_final_read,
        )

        captured = listen_for_phrase(
            _recognizer(),
            source,
            lambda: speech_playing,
            blocks_capture=lambda state: state,
        )

        self.assertIsNotNone(captured)
        self.assertTrue(captured.overlapped_capture)

    def test_discards_playback_overlap_before_retained_preroll(self):
        quiet = _pcm_buffer(0)
        speech = _pcm_buffer(400)
        speech_playing = True

        def finish_playback_before_phrase(read_count):
            nonlocal speech_playing
            if read_count == 2:
                speech_playing = False

        source = FakeAudioSource(
            [quiet, quiet, speech, speech, speech, quiet, quiet, quiet],
            before_read=finish_playback_before_phrase,
        )

        captured = listen_for_phrase(
            _recognizer(),
            source,
            lambda: speech_playing,
            blocks_capture=lambda state: state,
        )

        self.assertIsNotNone(captured)
        self.assertFalse(captured.started_with)
        self.assertFalse(captured.overlapped_capture)

    def test_stop_event_exits_before_reading_another_buffer(self):
        stop_event = Event()

        def request_stop(_read_count):
            stop_event.set()

        source = FakeAudioSource(
            [_pcm_buffer(0), _pcm_buffer(400)],
            before_read=request_stop,
        )

        captured = listen_for_phrase(
            _recognizer(),
            source,
            lambda: "unused",
            stop_event=stop_event,
        )

        self.assertIsNone(captured)
        self.assertEqual(source.stream.read_count, 1)


if __name__ == "__main__":
    unittest.main()
