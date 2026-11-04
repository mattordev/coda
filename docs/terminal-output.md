# Terminal output

When `CODA_STREAM_DISPLAY=true`, CODA writes the answer as visible text arrives.
Background listener, runtime, router and speech diagnostics wait until that
answer ends, so messages cannot split words. Cancellation closes the preview with
`[Response incomplete.]` before releasing queued diagnostics. Listening and
cancellation continue while messages are deferred.

The listener announces readiness or follow-up mode when its state changes,
rather than on every capture timeout. Recognition errors still appear.
While the microphone is idle, follow-up status updates on the next audio-buffer
read rather than waiting for a listening timeout. When voice cancellation is
enabled, overlapping audio is recognized and checked for a wakeword-stop phrase
before being rejected as echo, even if playback ended during capture. Recognition
can still delay follow-up readiness. Listeners without voice cancellation can
discard completed-playback captures early. This does not provide acoustic echo
cancellation or full duplex audio.

Deferred output retains at most 100 messages of up to 8,192 characters each. If this limit
is reached, the oldest messages are omitted with a count after the answer.
Writes to explicitly supplied files remain immediate. This coordination covers
CODA's application messages; third-party libraries writing directly to the
terminal can still emit their own output.

Set `CODA_DEBUG=true` for `[MODEL]` loading stages and elapsed-time updates, as
described in [model preparation](model-prewarming.md). Messages generated during
a streamed answer appear after it, keeping the answer readable. No new environment
settings are needed.
