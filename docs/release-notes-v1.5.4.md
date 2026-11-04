# v1.5.4 - Streaming & Runtime Polish

This patch release improves streamed speech continuity, terminal readability and
visibility into local model preparation. It builds on the v1.5.3 voice pipeline.

## Changes

- PocketTTS streaming prefers sentence and natural clause boundaries. Time spent
  playing the previous chunk no longer triggers a premature split, and queued
  text is consumed before testing idle timeouts. This reduces short mid-sentence
  utterances with sentence-ending pauses; long text still has bounded chunks.
- Listener, runtime, router and speech diagnostics wait until the streamed
  terminal answer ends, keeping background messages out of words and sentences.
  Cancellation marks the preview incomplete before releasing queued messages.
- Listener readiness and follow-up messages announce state changes instead of
  repeating on every capture timeout.
- Debug output reports local model preparation stages and elapsed time, including
  PocketTTS and faster-whisper cache misses. Startup preparation distinguishes
  queued tasks from active warming. Cached runtimes are reused as before.
- Local LLM requests report server wait and the first server event. That wait can
  include loading, queueing and prompt processing; it is not a cold-load measurement.
- Startup waits for enabled pre-warm tasks before announcing `C.O.D.A loaded in
  ... second(s)` and starting input. The load duration includes preparation;
  failed or skipped tasks retain normal fallback. Ctrl+C cancels the startup wait.
- Spoken times respect an existing AM/PM suffix, avoiding contradictory output
  such as "AM PM" and preserving midnight/noon meanings.
- Debug actions accept trailing sentence punctuation from speech recognition,
  including `Debug on.` and `Debug config reload.`.
- With debug disabled, startup uses a simple `Loading components...` banner;
  model-specific loading and preparation failure details are debug-only.

These changes were implemented in [#219](https://github.com/mattordev/coda/pull/219)
and [#221](https://github.com/mattordev/coda/pull/221), addressing
[#218](https://github.com/mattordev/coda/issues/218) and
[#220](https://github.com/mattordev/coda/issues/220).

## Configuration

No new environment variables, dependencies or model settings are required.

- `CODA_STREAM_SPEECH=true` enables incremental speech (the existing default).
- `CODA_STREAM_DISPLAY=true` enables incremental terminal display independently
  of speech. Its default remains `false`.
- `CODA_DEBUG=true` enables `[MODEL]` stages and elapsed updates during long waits.
  Messages generated during a streamed answer appear after that answer.
- Existing `CODA_PREWARM_LLM`, `CODA_PREWARM_TTS` and `CODA_PREWARM_STT` choices
  remain opt-in. Debug reporting does not enable pre-warming or change provider order.

Restart CODA after updating or changing configuration. Existing private `.env`
values and workspace permissions should be retained.

## Validation and limits

Regression coverage includes speech chunk timing and queued text, concurrent
terminal output, cancellation, bounded diagnostic retention, debug gating and
model cache reuse. The release candidate is checked on Windows and Linux with
Python 3.11 and 3.12; `python -m utils.update_releases v1.5.4` verifies that the
release tag agrees with `version.json`.

Deferred application diagnostics retain up to 100 messages of 8,192 characters
each, with an omission count on overflow. Third-party libraries writing directly
to the terminal remain outside this coordination. Native loaders do not expose
reliable percentage progress, so model feedback shows stages and elapsed time.
Streaming does not remove model loading, reasoning or prompt-processing latency,
and these fixes do not provide acoustic echo cancellation or full duplex audio.

See [incremental speech](incremental-speech.md),
[terminal output](terminal-output.md) and
[model preparation](model-prewarming.md) for the detailed behavior and limits.
