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
supports every option. Built-in adapters enforce the output budgets below.

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

## Output budgets and completion

| Workload | Default maximum generated tokens | Environment setting |
| --- | --- | --- |
| Classification | 256 | `CODA_MAX_TOKENS_CLASSIFICATION` |
| Long structured argument extraction | 2048 | `CODA_MAX_TOKENS_EXTRACTION` |
| Routine tool interpretation | 512 | `CODA_MAX_TOKENS_INTERPRETATION` |
| Conversation | inherit | `CODA_MAX_TOKENS_CONVERSATION` |
| Deliberate reasoning | inherit | `CODA_MAX_TOKENS_REASONING` |

Set a positive integer to override; blank/invalid/nonpositive settings use the
workload default. A cap includes any internal reasoning counted by the provider;
512 generated tokens does not guarantee 512 visible answer tokens. These are
request limits, separate from the existing timeouts and server defaults.

The combined intent/argument classifier normally uses 256. If a catalogue entry
has a structured input schema and the JSON-escaped user message's UTF-8 byte
length plus 128 exceeds that budget, it uses the larger extraction allowance
(2048 by default). This conservative estimate reserves space to copy long
arguments without assuming a tokenizer; catalogue descriptions cannot increase
the allowance. Very long/escaped arguments can still exceed it. Validation then
rejects the result; CODA never executes an incomplete selection. Raising the
classification setting above extraction never reduces it. Argument/schema and
permission validation still happen before dispatch.

OpenAI and xAI use `max_completion_tokens`; OpenRouter and Gemini's compatible chat
adapters use `max_tokens`; llama.cpp uses `max_tokens`; Ollama uses
`options.num_predict`. No provider is retried merely to discover parameter
support. See [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[OpenRouter parameters](https://openrouter.ai/docs/api_reference/parameters),
[Gemini compatibility](https://ai.google.dev/gemini-api/docs/openai),
[xAI Chat Completions](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions), and
[Ollama parameters](https://docs.ollama.com/modelfile#valid-parameters-and-values).

Every adapter rejects explicit length termination, including syntactically valid
JSON. Capped calls also require a terminal finish marker, llama.cpp `[DONE]`, or
Ollama `done: true`; EOF alone is incomplete. Unsupported text completion reasons
(including filtering or tool calls from a text-only request) are rejected. Legacy
uncapped calls without completion metadata retain their existing behavior;
unknown usage counts stay unknown. Provider errors and cancellation discard any
accumulated partial answer. The service therefore cannot commit it to history or
pass a partial classifier selection to tool execution.

xAI documents its cap as visible output only, excluding reasoning and function
calls. Other providers commonly count reasoning within the completion cap.
Timeouts therefore remain necessary; an output cap alone cannot bound hidden
reasoning time consistently across providers.

Budget/incomplete-answer failures can fall back through the existing privacy
order but do not mark the server unhealthy for ten minutes. Actual connection
and timeout failures retain the existing health policy. If interpretation cannot
obtain a complete answer it explicitly reports incompleteness; it never labels
the generated fragment a completed summary.
