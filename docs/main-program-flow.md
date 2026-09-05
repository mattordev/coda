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

Follow-up permission and audible playback state are snapshotted when the
accepted phrase begins. They are not taken when the listener first starts
waiting or after transcription finishes. CODA's phrase-aware adapter mirrors
SpeechRecognition 3.17.0's non-streaming voice-activity detection because the
library's streaming iterator exposes short candidates without reporting when
one is rejected. This lets CODA replace the snapshot after noise and associate
the final `AudioData` with the accepted phrase-start buffer's state.

A wakeword-free phrase that began before the follow-up window opened is ignored
without closing the newly opened window. A phrase that began while CODA was
audibly speaking is also ignored, preventing assistant playback echoed through
the microphone from feeding back into the request queue. An exact wake-word
stop command, such as `coda stop`, is deliberately handled before this echo
filter so spoken cancellation remains available during playback.

The execution worker processes queued requests one at a time. It runs intent
routing and command dispatch, or LLM fallback for unmatched input. LLM fallback
passes through the privacy-aware provider router, which resolves the configured
local and cloud provider order and attempts eligible providers until one
succeeds, the request is cancelled or no providers remain. Responses are
submitted to the speech worker, while execution and speech outcomes are
published to the event worker for follow-up-state handling.

The main thread remains available for the Ctrl+B mode toggle. If the global
keyboard hook is unavailable, CODA disables only that toggle and continues in
voice mode. If a stopped listener is still exiting when voice mode is requested
again, the voice worker defers the restart until that listener has finished.

## Manual Mode

Manual input still requires a wake word. The main loop handles:

- `quit` or `exit`: return through graceful shutdown.
- `voice` or `/voice`: switch to voice mode and start the voice worker.
- A wake-word request: strip the wake word, create an `InputSource.MANUAL`
  `RuntimeRequest` and submit it to the shared request queue.

Manual commands use the same intent registry and LLM fallback, but their
execution is serialised with voice requests on the execution worker. Command
speech and manual LLM replies both use the configured speech worker.

## Cancellation

In voice or manual mode, a wake-word stop phrase calls the runtime cancellation
boundary. Manual mode keeps its input prompt available during speech, so a
typed command such as `coda stop` can interrupt playback. Cancellation signals
the event belonging to the active request and the oldest outstanding speech
task before stopping that task's active provider session. Speech remains
cancellable while it is queued, resolving a provider or playing. Interruptible
LLM provider calls close their active response streams or sessions and discard
partial provider output. Cancellation also prevents the router from continuing
to another fallback provider. A cancelled execution result does not add partial
text to conversation history, dashboard output or speech playback. After
execution has completed, cancelling its tracked speech prevents or stops the
audio but does not retract text already recorded in conversation history or
dashboard state. The same applies to command speech recorded before submission.

## Shutdown

Manual exit and Ctrl+C both execute the idempotent `shutdown_runtime()` path:

1. Close follow-up state and stop voice input.
2. Cancel active execution and join the execution worker.
3. Disconnect speech submission, cancel outstanding speech and stop playback.
4. Stop the event worker.
5. Stop the heartbeat worker.

Every worker join is bounded by a timeout. Ctrl+C is handled without exposing a
`KeyboardInterrupt` traceback.

## Dashboard State Touchpoints

- Voice transcripts: `voice_recognizer` calls
  `dashboard_state.record_user_message()`.
- Queued LLM responses: the execution path calls
  `dashboard_state.record_ai_response()` before speech submission.
- Command speech: `speak_response()` records the response before queueing it.
- Liveness: the heartbeat worker calls
  `dashboard_state.touch_heartbeat(source="main")`.
- The Flask dashboard reads the persisted snapshot from `/api/state`.

## Related Documentation

- [Concurrent Runtime](concurrent-runtime.md)
- [Command Modules](command-modules.md)
- [Intent Routing](intent-routing.md)
- [LLM Providers](providers.md)
- [Privacy Routing](privacy-routing.md)
