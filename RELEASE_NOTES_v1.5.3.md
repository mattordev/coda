# v1.5.3 — Incremental Voice Pipeline

CODA can begin speaking ordinary LLM answers before generation finishes.
Visible text flows through a bounded speech accumulator into ordered playback,
using the existing privacy routing and request cancellation.

- Optional visible-content callbacks on all six built-in LLM adapters exclude
  reasoning channels and preserve legacy provider compatibility.
- Sentence-first speech protects versions, decimals, abbreviations, URLs and
  complete Markdown links; fenced code and oversized technical tokens are
  omitted from speech while the exact final semantic answer is retained.
- One warm PocketTTS runtime and one ffplay process serve a streamed answer.
  Bounded buffering applies cancellation-aware backpressure.
- Cancellation discards unspoken text and stops audio. Failed/unfinished
  generation does not commit a partial turn. A failed visible prefix cannot
  trigger duplicate provider fallback.
- The dashboard/history receive one final answer. Optional terminal previews
  are independent of incremental speech and mark interrupted output incomplete.
- Content-free timings correlate request acceptance, visible output, first
  speech chunk, first PCM, playback pipe write and completion. Audio timing is
  a playback proxy, not acoustic loopback.

`CODA_STREAM_SPEECH=true` enables incremental conversation speech by default;
`CODA_STREAM_DISPLAY=false` keeps finalized terminal output by default. Native
commands, legacy providers and MCP's validated/sanitized interpretation path
remain compatible. See [configuration and architecture](docs/incremental-speech.md).

Output streaming does not solve acoustic full duplex or echo-free barge-in;
that discovery is tracked separately in #206. Existing provider deadlines still
include backpressure waits, and a late stream error may leave an incomplete
prefix already heard without committing it as a successful answer.
