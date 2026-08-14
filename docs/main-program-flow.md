# C.O.D.A Main Program Flow

This document summarises startup, input modes and shutdown. For worker,
message, cancellation and command-extension details, see
[Concurrent Runtime](concurrent-runtime.md).

## Startup

`main.py` calls `coda_runtime.main()`, which guarantees cleanup around the
runtime body.

1. Parse manual-mode and microphone flags.
2. Optionally list microphones and exit.
3. Check for an available CODA update.
4. Load command modules and wake words, rebuilding first-time files when
   needed.
5. Configure and validate the intent router.
6. Start the heartbeat, execution, event and speech workers.
7. Configure `speak_response()` to submit asynchronous speech tasks.
8. Start the voice-input worker unless manual mode was requested.
9. Enter the main input-mode loop.

## Voice Mode

The voice worker owns microphone capture and speech-to-text. It records the
transcript, applies wake-word and follow-up rules, then submits a
`RuntimeRequest` to the shared request queue.

The execution worker processes queued requests one at a time. It runs intent
routing and command dispatch, or LLM fallback for unmatched input. Responses
are submitted to the speech worker, while execution and speech outcomes are
published to the event worker for follow-up-state handling.

The main thread remains available for the Ctrl+B mode toggle. If the global
keyboard hook is unavailable, CODA disables only that toggle and continues in
voice mode.

## Manual Mode

Manual input still requires a wake word. The main loop handles:

- `quit` or `exit`: return through graceful shutdown.
- `voice` or `/voice`: switch to voice mode and start the voice worker.
- A wake-word request: strip the wake word and call the shared command and
  intent pipeline synchronously.

Manual commands use the same intent registry and LLM fallback, but their
execution currently bypasses the runtime request queue. Speech requested by a
command still uses the configured speech worker.

## Cancellation

In voice mode, a wake-word stop phrase calls the runtime cancellation boundary.
It signals the event belonging to the active request and stops any active
speech session. Interruptible OpenAI and Ollama calls close their response
streams and discard partial provider output. A cancelled queued LLM result does
not add partial text to conversation history, dashboard output, speech playback
or provider fallback. A command can submit speech while its body is running,
so cancellation does not retract command output already queued or recorded.
Cancelling playback after execution has completed likewise does not retract
text that was already recorded.

## Shutdown

Manual exit and Ctrl+C both execute the idempotent `shutdown_runtime()` path:

1. Close follow-up state and stop voice input.
2. Cancel active execution and join the execution worker.
3. Disconnect speech submission and stop speech playback.
4. Stop the event worker.
5. Stop the heartbeat worker.

Every worker join is bounded by a timeout. Ctrl+C is handled without exposing a
`KeyboardInterrupt` traceback.

## Dashboard State Touchpoints

- Voice transcripts: `voice_recognizer` calls
  `dashboard_state.record_user_message()`.
- Queued voice LLM responses: the execution path calls
  `dashboard_state.record_ai_response()` before speech submission.
- Command speech: `speak_response()` records the response before queueing it.
- Liveness: the heartbeat worker calls
  `dashboard_state.touch_heartbeat(source="main")`.
- The Flask dashboard reads the persisted snapshot from `/api/state`.

## Related Documentation

- [Concurrent Runtime](concurrent-runtime.md)
- [Command Modules](command-modules.md)
- [Intent Routing](intent-routing.md)
- [Privacy Routing](privacy-routing.md)
