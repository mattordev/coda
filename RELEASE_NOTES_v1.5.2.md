# v1.5.2 — LLM Workload Optimisation

Routine classification and tool-result interpretation use request-scoped
thinking controls and bounded output instead of inheriting unlimited reasoning.
The configured model, hardware and llama.cpp server defaults remain unchanged.

## Changes

- Immutable generation options reach all six provider adapters while preserving
  existing response/error and injected-adapter compatibility.
- Local routine thinking defaults off; conversation inherits model behavior;
  explicit analysis prefixes opt into reasoning. llama.cpp uses the verified
  Qwen request flag and Ollama uses advertised model capabilities.
- Default caps: 256 classification, 2048 long structured arguments, 512 routine
  interpretation. Each provider receives its supported parameter.
- Length termination and incomplete capped streams discard partial replies,
  including valid-looking JSON. Budget failures retain privacy-ordered fallback
  without treating an exhausted output budget as a provider outage.
- Semantic answers retain versions, numbers, paths, IDs and quoted filenames.
  Deterministic TTS copies handle pronunciation, including fractional zeros.
- Runtime IDs correlate classifier, conversation, interpretation and fallback
  attempts. Active cancellation reaches the classifier's provider work.
- Optional telemetry separates observed generated and visible token arrival,
  native prompt/decode/load time and actually reported reasoning usage.
- Public evaluation fixtures cover all six native intents, structured MCP
  arguments, noisy/ambiguous/unsupported requests, numeric facts and injection.

## Configuration and validation

See [workload controls](docs/llm-workloads.md),
[semantic text and speech](docs/semantic-response-and-speech.md),
[request timing definitions](docs/request-latency-telemetry.md), and the
[evaluation report](docs/llm-workload-benchmark.md).
Example settings are in `docs/.env.example`; existing private settings are
preserved by the updater. Thinking support varies by provider/model, and a cap
does not guarantee a fixed amount of visible output or universal accuracy.

Incremental LLM-to-TTS streaming belongs to v1.5.3. This release still validates
a complete generated response before queueing it for speech.
