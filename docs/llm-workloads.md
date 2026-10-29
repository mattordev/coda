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

This contract is the first v1.5.2 development step (#184). Workload defaults,
thinking protocol translation and output-budget enforcement are tracked by
#185/#186; accepting an options object alone does not promise that a provider
supports every option. Privacy routing, permission checks, cancellation and
history rollback retain their existing ownership.
