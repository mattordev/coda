# LLM workload controls

`ai.providers.generation.GenerationOptions` carries immutable controls for one
request: workload, optional thinking choice and optional output-token budget.
`None` inherits provider/model behavior. It never changes server defaults or
the configured model. Workloads distinguish conversation, classification,
argument extraction, tool interpretation and deliberate reasoning.

Conversation routing, history-aware service calls and stateless service calls
accept an optional `options` keyword. Built-in provider entrypoints also accept
it, while existing calls and response/error unpacking remain compatible.
Injected legacy adapters without that keyword are inspected before invocation
and receive their original call shape. A TypeError is never used to retry a
provider call, because the first call may already have accepted the request.

Privacy routing, permission checks, cancellation and history rollback retain
their existing ownership. Accepting an options object does not mean a provider
supports every option. Output-budget enforcement is tracked by #186.

## Thinking policy

| Workload | Default | Environment setting |
| --- | --- | --- |
| Classification | off | `CODA_THINKING_CLASSIFICATION` |
| Argument extraction | off | `CODA_THINKING_EXTRACTION` |
| Routine tool interpretation | off | `CODA_THINKING_INTERPRETATION` |
| Conversation | inherit | `CODA_THINKING_CONVERSATION` |
| Deliberate reasoning | on | `CODA_THINKING_REASONING` |

Values `on`/`true`/`1`, `off`/`false`/`0`, and `auto`/`inherit` select a boolean
override or the model default. Missing/invalid values use the workload default.
Explicit `GenerationOptions` supplied by a caller take precedence over workload
selection; no environment value is written back or changed by a request.

User requests beginning with `think carefully`, `think step by step`,
`reason carefully`, `reason step by step`, `analyze in depth` (also `analyse`),
or `reason:` opt into deliberate reasoning for conversation/tool interpretation.
Embedded mentions and tool-result content do not select this policy.
Classification stays a routine operation even when identifying such a request.
These prefixes request internal reasoning; reasoning-channel text is never
returned as the answer, displayed or spoken. The visible answer can still
contain an explanation requested by the user.

For llama.cpp, requests use `chat_template_kwargs.enable_thinking`. This works
with the verified local Qwen hybrid template; other templates may ignore the
flag or lack switchable reasoning. `CODA_LLAMACPP_THINKING_CONTROL=false` omits
the field for older/incompatible servers. No server-wide defaults/model settings
are changed. See the [llama.cpp API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

Ollama probes `/api/show` once per selected server/model and configuration
generation, with a five-second maximum probe timeout inside the existing call
deadline. It sends `think` only when the model advertises the requested boolean.
Missing metadata, models with only named levels, and unsupported controls inherit
the model behavior. `debug reload` clears the capability cache. It does not guess
from model names or retry accepted generations to discover support. See
[Ollama thinking controls](https://docs.ollama.com/capabilities/thinking).
Other provider adapters currently retain their own thinking defaults; they do
not receive llama.cpp template arguments. Hardware/model and cold-start settings
remain unchanged.
