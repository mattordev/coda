# Incremental speech

v1.5.3 can start speaking ordinary LLM conversation while the provider is still
generating its answer. All six built-in LLM adapters expose visible content
deltas. Reasoning channels are excluded. Existing injected providers without
generation options continue through complete-response speech.

## Operator settings

`CODA_STREAM_SPEECH=true` is the default. Set it to `false` to restore waiting
for the complete answer before speech. `CODA_STREAM_DISPLAY=false` keeps the
terminal's final-answer display; enable it for an incremental terminal preview
independently of incremental speech. Restart CODA after changing these flags.
No model selection, thinking policy or server-wide setting changes are needed.
Normal LLM and TTS provider-order settings still apply.

PocketTTS retains one warm model and uses one ffplay process across the spoken
chunks of an answer. Other TTS providers receive ordered complete sentence or
phrase sessions. Providers that cannot stream visible text still work through
the full-answer path. Native commands and MCP interpretation keep their
complete-response path; MCP interpretation's final sanitization, privacy and
presentation gates run before any derived speech. A classifier's JSON and tool
argument extraction never enter speech or incremental display.

## Chunking, buffering and failure behavior

Speech prefers sentence boundaries with lookahead. Size, phrase and elapsed
fallbacks avoid waiting indefinitely for a long sentence. The accumulator keeps
at most 1,024 characters and targets 320-character chunks; its elapsed fallback
starts after one second and requires at least 40 complete characters. Tokens
are not spoken one by one. Trailing lexical tokens remain buffered so partial
decimals, versions, abbreviations and URLs are not cut into misleading values.
Markdown links are rendered as complete label/target pairs. Fenced code and
oversized unbroken technical tokens are omitted from speech with a short notice;
the semantic answer remains exact in final history and the dashboard.

An eight-slot queue holds at most 256 characters per delta. When full, the
producer blocks with cancellation polling rather than growing an unlimited
speech backlog. The existing provider deadline includes this wait: very long
answers or a slow/cold speech backend can exhaust it. Use a suitable
`CODA_LLM_TIMEOUT`, warm PocketTTS when practical, or disable incremental speech
for unusually long output. Streaming does not remove prompt processing or
hidden reasoning latency.

The same request ID and cancellation event reach generation, the speech queue,
chunking, PCM playback and worker events. `coda stop` stops generation and audio,
discards unspoken text and keeps follow-up closed. Cancellation or provider
failure before successful generation commits no partial turn or dashboard
answer. If a complete answer has already been generated and committed,
interrupting its later playback retains that complete semantic turn.

Fallback is allowed before visible output begins. Once a provider emits a
visible prefix, a failure cannot silently replay a second answer. Speech
providers are selected before consuming the stream; a failure after consumption
aborts the backlog instead of replaying audio. A failed/cancelled optional
terminal preview is explicitly marked incomplete. Prefixes already heard or
seen cannot be retracted; an eventual token-limit or network error can therefore
leave an incomplete spoken prefix. It never becomes a successful full answer.

## Latency measurements

`logs/speech/active-session.jsonl` contains at most 1,000 records in the current
process, separate from provider-attempt logs. Records contain request identity,
mode, outcome and timings, never prompts, answer text or reasoning. Durations
use a high-resolution monotonic clock starting at `RuntimeRequest` creation,
including queue and intent time. Fields in seconds:

| Field | Meaning |
| --- | --- |
| `first_visible_seconds` | First visible conversation delta, or finalized non-streaming response |
| `first_chunk_seconds` | First complete speakable chunk ready |
| `first_pcm_seconds` | First PocketTTS PCM received |
| `playback_start_seconds` | First successful PCM write and flush into ffplay |
| `first_audio_seconds` | Alias for the playback pipe-write proxy |
| `generation_complete_seconds` | Execution/generation result ready |
| `completion_seconds` | Speech processing finished, cancelled or failed |

The pipe-write proxy establishes when audio enters playback, not when a sound
physically leaves the speaker. Player/device buffering remains outside it.
Legacy TTS providers without PCM observations leave audio fields `null`.
Provider-attempt first-token, prompt and decode timings remain available in
[request telemetry](request-latency-telemetry.md); their origin is the provider
attempt rather than runtime acceptance.

## Validation and acoustic scope

Run `python -m benchmarks.incremental_speech --repeat 3 --output results.json`
for synthetic token/PCM flow, or add `--run-local --base-url <private-server>
--model <configured-model> --tts pockettts` for real local generation and
playback. The live mode preloads PocketTTS before measurement, alternates mode
order, requests per-call thinking off with a 512-token cap and records provider
timings. It uses public fixture text and never executes a tool. The first LLM
request may still incur cold loading; distinguish it from warm comparisons.
The harness does not unload the server model automatically.

Output streaming does not establish acoustic full duplex. The microphone's
playback-echo filter and explicit stop exception remain. Echo cancellation,
voice activity/turn taking and genuine barge-in are separately tracked in
[#206](https://github.com/mattordev/coda/issues/206).
