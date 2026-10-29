# Request latency telemetry

Runtime requests now carry their full request ID through command execution,
intent classification, conversation routing, privacy-ordered provider fallback
and tool-result interpretation. The console prints its short prefix; each
provider attempt's `trace_id` contains the full ID. Direct command calls create
an ID when needed. Nested calls and cancellable worker threads inherit it;
unrelated threads and later requests do not. Attempt sequences belong to a
provider operation/purpose within a request, rather than numbering every stage.

The same request context carries the cancellation event into classification.
Cancelling while a classifier is waiting on generation releases its provider
resources through the existing cancellation scope, records a cancelled attempt
and prevents a late selection from reaching tool execution or conversational
fallback. Context is restored after successful, failed and cancelled calls.

The privacy-safe JSONL attempt records and aggregate snapshots include these
optional measurements in `metrics` (seconds unless noted):

| Field | Meaning |
| --- | --- |
| `time_to_first_generated_token_seconds` | First observed generated text in a visible or exposed reasoning channel |
| `time_to_first_visible_token_seconds` | First nonempty visible answer delta |
| `time_to_first_token_seconds` | Existing field, retained as an alias for first visible arrival |
| `prompt_duration_seconds` | Provider-reported prompt processing time |
| `decode_duration_seconds` | Provider-reported generation time |
| `load_duration_seconds` | Provider-reported model load time (Ollama) |
| `reasoning_tokens` | Actual provider-reported reasoning count, never an estimate |

Arrival times start at the provider attempt's collector creation, including
client/model discovery overhead where applicable. They measure observed stream
arrival, not a server-side timestamp for each token. If reasoning is hidden,
first generated arrival is only the earliest exposed text; it cannot reveal
earlier unseen work. An empty answer can have reasoning arrival but no visible
arrival. No hidden reasoning text is retained, printed or sent to TTS.
Native HTTP streams read lines without waiting for a 512-byte client buffer,
so short deltas become observable promptly; network/server buffering can still
affect arrival time.

llama.cpp's top-level `timings.prompt_ms`/`predicted_ms` supply prompt/decode
durations. Prompt cache reuse means native prompt processing can cover only
uncached input; it is not the entire request wall time. Ollama's
`prompt_eval_duration`, `eval_duration` and `load_duration` are converted from
nanoseconds. Decode throughput uses native decode duration when available;
otherwise the existing total-duration fallback remains. Usage counters come
from the provider, and reported reasoning counts use
`usage.completion_tokens_details.reasoning_tokens`. Counts are not inferred
from answer characters or the difference between output and visible tokens.

Missing/invalid values remain `null`; actual zero values remain zero. These are
additive optional schema-1 JSON fields: older retained records still load and
have no fabricated samples in the new aggregates. Existing dashboards keep the
visible-token field; detailed generated/visible/native timings are available in
attempt logs and aggregate snapshots. Model inference time, input queuing and
audio playback latency remain separate quantities.
