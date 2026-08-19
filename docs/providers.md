# LLM Providers

CODA supports multiple local and cloud LLM providers through a shared provider
registry and routing system.

The router decides which providers are eligible for a request and the order in
which they are attempted. Individual provider adapters handle provider-specific
API calls, streaming, cancellation and model configuration.

For the privacy rules that determine whether cloud providers can receive a
request, see [Privacy Routing](privacy-routing.md).

## Supported Providers

CODA currently supports six LLM providers:

| Provider   | Type  | API key              | Model setting           | Base URL setting         |
| ---------- | ----- | -------------------- | ----------------------- | ------------------------ |
| OpenAI     | Cloud | `OPENAI_API_KEY`     | `CODA_OPENAI_MODEL`     | Built in                 |
| Gemini     | Cloud | `GEMINI_API_KEY`     | `CODA_GEMINI_MODEL`     | Built in                 |
| Grok       | Cloud | `XAI_API_KEY`        | `CODA_GROK_MODEL`       | Built in                 |
| OpenRouter | Cloud | `OPENROUTER_API_KEY` | `CODA_OPENROUTER_MODEL` | Built in                 |
| Ollama     | Local | Not required         | `CODA_OLLAMA_MODEL`     | `CODA_OLLAMA_BASE_URL`   |
| llama.cpp  | Local | Not required         | `CODA_LLAMACPP_MODEL`   | `CODA_LLAMACPP_BASE_URL` |

Cloud providers are considered configured when their required API key is
present. Local providers do not require an API key, although the configured
local server still needs to be reachable when CODA attempts to use it.

OpenRouter additionally requires `CODA_OPENROUTER_MODEL` to contain a model ID
before it can generate a response.

## Provider Ordering

Cloud and local provider order is configured separately:

```text
CODA_CLOUD_PROVIDERS=openrouter,gemini,grok,openai
CODA_LOCAL_PROVIDERS=llamacpp,ollama
```

Provider names are comma-separated and evaluated from left to right.

The configured lists are filtered through the provider registry before routing:

- Unknown provider names are ignored.
- Cloud providers placed in the local list are skipped.
- Local providers placed in the cloud list are skipped.
- Cloud providers without their required API key are skipped.
- Local providers remain eligible without an API key.

The privacy router then combines the cloud and local lists according to the
request's privacy level.

## Privacy-Aware Routing

### Low Risk

Low-risk requests prefer cloud providers and then fall back to local providers.

With:

```text
CODA_CLOUD_PROVIDERS=openrouter,gemini,grok,openai
CODA_LOCAL_PROVIDERS=llamacpp,ollama
```

the resulting order can be:

```text
openrouter -> gemini -> grok -> openai -> llamacpp -> ollama
```

### Medium Risk

Medium-risk requests prefer local providers first.

If the configured privacy policy permits cloud fallback, the cloud providers
are appended after the local providers.

Using the same configuration, the order can be:

```text
llamacpp -> ollama -> openrouter -> gemini -> grok -> openai
```

Cloud attempts receive the privacy-safe rendering selected by the active
privacy policy rather than automatically receiving the raw sensitive values.

### High Risk

High-risk requests stay local by default.

With the default balanced privacy mode and
`CODA_HIGH_RISK_CLOUD_FALLBACK=block`, only the configured local providers are
eligible.

Cloud fallback for a high-risk request only occurs when the configured privacy
policy explicitly permits it.

See [Privacy Routing](privacy-routing.md) for the complete privacy policy,
sanitisation and cloud-history behaviour.

## Fallback Behaviour

A provider failure does not immediately fail the whole request.

CODA attempts eligible providers in order until:

- a provider returns a successful response;
- the request is cancelled; or
- no eligible providers remain.

For example:

```text
openrouter -> gemini -> grok -> openai -> llamacpp
```

If OpenRouter fails, Gemini can be attempted next. If Gemini fails, CODA can
continue to Grok, then OpenAI, and finally llama.cpp if the current privacy
policy permits those providers.

A provider that has failed recently can enter a temporary cooldown. While it is
in cooldown, CODA skips that provider and continues to the next eligible
provider.

Cancellation is handled differently from an ordinary provider failure. If the
active request is cancelled, CODA stops the request and does not continue
through the fallback list.

Provider failure and cancellation do not commit a failed or partial exchange to
conversation history.

## Conversation History

CODA maintains one internal conversation history but renders that history
differently depending on provider type.

- Local providers receive the raw conversation content.
- Cloud providers receive cloud-safe conversation content.
- Sensitive values can be sanitised or replaced with a cloud-safe summary.
- Content blocked by the privacy policy is not sent to a cloud provider.
- Falling back from one provider to another does not bypass the privacy rules.

This allows local and cloud providers to participate in the same conversation
without automatically exposing sensitive local-only history to cloud services.

## Cloud Providers

### OpenAI

OpenAI is a cloud provider using the OpenAI API.

Configuration:

```text
OPENAI_API_KEY=
CODA_OPENAI_MODEL=gpt-4o-mini
```

`OPENAI_API_KEY` is required when OpenAI is included in
`CODA_CLOUD_PROVIDERS`.

If `CODA_OPENAI_MODEL` is unset or blank, CODA uses:

```text
gpt-4o-mini
```

Example:

```text
CODA_CLOUD_PROVIDERS=openai
OPENAI_API_KEY=your-key-here
CODA_OPENAI_MODEL=gpt-4o-mini
```

Do not commit a real API key to the repository.

### Google Gemini

Gemini is a cloud provider accessed through Google's OpenAI-compatible API
interface.

Configuration:

```text
GEMINI_API_KEY=
CODA_GEMINI_MODEL=gemini-3.7-flash
```

`GEMINI_API_KEY` is required when Gemini is included in
`CODA_CLOUD_PROVIDERS`.

If `CODA_GEMINI_MODEL` is unset or blank, CODA uses:

```text
gemini-3.7-flash
```

Example:

```text
CODA_CLOUD_PROVIDERS=gemini
GEMINI_API_KEY=your-key-here
CODA_GEMINI_MODEL=gemini-3.7-flash
```

Do not commit a real API key to the repository.

### xAI Grok

Grok is a cloud provider using xAI's OpenAI-compatible API.

Configuration:

```text
XAI_API_KEY=
CODA_GROK_MODEL=grok-4.5
```

`XAI_API_KEY` is required when Grok is included in
`CODA_CLOUD_PROVIDERS`.

If `CODA_GROK_MODEL` is unset or blank, CODA uses:

```text
grok-4.5
```

Example:

```text
CODA_CLOUD_PROVIDERS=grok
XAI_API_KEY=your-key-here
CODA_GROK_MODEL=grok-4.5
```

Do not commit a real API key to the repository.

### OpenRouter

OpenRouter is a cloud provider using OpenRouter's OpenAI-compatible API.

Configuration:

```text
OPENROUTER_API_KEY=
CODA_OPENROUTER_MODEL=
```

Both an API key and a model ID are required for a usable OpenRouter
configuration.

Unlike the other cloud adapters, CODA deliberately does not provide a default
OpenRouter model and does not maintain a hard-coded allow-list of model names.

`CODA_OPENROUTER_MODEL` accepts an arbitrary valid OpenRouter model ID.

Examples:

```text
CODA_OPENROUTER_MODEL=openrouter/free
```

or another exact model ID supported by OpenRouter:

```text
CODA_OPENROUTER_MODEL=provider/model-name
```

This lets the available OpenRouter catalogue change without requiring CODA to
be updated simply to recognise a new model name.

Example configuration:

```text
CODA_CLOUD_PROVIDERS=openrouter
OPENROUTER_API_KEY=your-key-here
CODA_OPENROUTER_MODEL=openrouter/free
```

Do not commit a real API key to the repository.

## Local Providers

### Ollama

Ollama is a local provider using Ollama's HTTP API.

Configuration:

```text
CODA_OLLAMA_BASE_URL=http://localhost:11434
CODA_OLLAMA_MODEL=
```

`CODA_OLLAMA_BASE_URL` defaults to:

```text
http://localhost:11434
```

An API key is not required.

#### Ollama Model Selection

If `CODA_OLLAMA_MODEL` is set, CODA uses that model directly.

For example:

```text
CODA_OLLAMA_MODEL=llama3.2:3b
```

If the setting is blank, CODA queries:

```text
/api/tags
```

on the configured Ollama server and discovers the models available there.

CODA currently prefers:

```text
nemotron-3-nano:4b
```

when that model is present. Otherwise, it uses the first usable model reported
by Ollama.

The automatically selected model is cached until provider configuration is
reloaded.

If model discovery fails, CODA reports the error and suggests setting
`CODA_OLLAMA_MODEL` explicitly.

Ollama also checks `/api/ps` before generation to determine whether the selected
model is already loaded. A separate cold-start timeout can be configured for
requests that may need to load a model first:

```text
CODA_OLLAMA_COLD_START_TIMEOUT=120
```

### llama.cpp

llama.cpp is a local provider designed for a llama.cpp server exposing its
OpenAI-compatible HTTP endpoints.

Configuration:

```text
CODA_LLAMACPP_BASE_URL=http://localhost:8080
CODA_LLAMACPP_MODEL=
```

`CODA_LLAMACPP_BASE_URL` defaults to:

```text
http://localhost:8080
```

An API key is not required.

#### llama.cpp Model Discovery

If `CODA_LLAMACPP_MODEL` is set, CODA uses that model ID directly.

For example:

```text
CODA_LLAMACPP_MODEL=my-model
```

If the setting is blank, CODA queries:

```text
/v1/models
```

on the configured llama.cpp server.

CODA reads the first model returned in the response and uses its `id` value for
subsequent requests.

The discovered model ID is cached until provider configuration is reloaded.

This supports llama.cpp deployments where the model is selected when the server
starts rather than supplied by CODA itself.

If the server cannot be queried, reports no models, or returns a model without a
usable ID, CODA reports a model-resolution error and suggests setting
`CODA_LLAMACPP_MODEL` explicitly.

Generation is sent to the server's OpenAI-compatible endpoint:

```text
/v1/chat/completions
```

## Timeouts

The general LLM request timeout is configured with:

```text
CODA_LLM_TIMEOUT=45
```

The default is 45 seconds.

Ollama also has a separate cold-start timeout:

```text
CODA_OLLAMA_COLD_START_TIMEOUT=120
```

This gives a local Ollama model additional time to load when it is not already
resident.

## Diagnostics and Debug Output

Each provider exposes a diagnostic description that includes its current model
or model-resolution state.

Examples:

```text
openai (model: gpt-4o-mini)
gemini (model: gemini-3.7-flash)
grok (model: grok-4.5)
openrouter (model: openrouter/free)
ollama (model: nemotron-3-nano:4b)
llamacpp (model: my-model)
```

If a local provider cannot resolve a model, its description reports the model
resolution failure rather than silently selecting an unknown model.

With runtime debugging enabled, routing output shows the resolved privacy risk,
provider order and provider attempts.

Example:

```text
[DEBUG - ROUTER] Privacy risk: 0.0
[DEBUG - ROUTER] Provider order: ['openrouter', 'gemini', 'grok', 'openai', 'llamacpp']
[DEBUG] Primary provider being checked: openrouter
[ROUTER] Low risk -> trying providers in order: ['openrouter', 'gemini', 'grok', 'openai', 'llamacpp']
[ROUTER] trying provider openrouter
[ROUTER] openrouter succeeded
```

This makes it possible to confirm which providers were eligible, which provider
was attempted and which provider ultimately handled the request.

## Example Configuration

A mixed local and cloud setup could look like:

```text
# Cloud provider ordering
CODA_CLOUD_PROVIDERS=openrouter,gemini,grok,openai

# OpenAI
OPENAI_API_KEY=
CODA_OPENAI_MODEL=gpt-4o-mini

# Gemini
GEMINI_API_KEY=
CODA_GEMINI_MODEL=gemini-3.7-flash

# Grok
XAI_API_KEY=
CODA_GROK_MODEL=grok-4.5

# OpenRouter
OPENROUTER_API_KEY=
CODA_OPENROUTER_MODEL=openrouter/free

# Local provider ordering
CODA_LOCAL_PROVIDERS=llamacpp,ollama

# llama.cpp
CODA_LLAMACPP_BASE_URL=http://localhost:8080
CODA_LLAMACPP_MODEL=

# Ollama
CODA_OLLAMA_BASE_URL=http://localhost:11434
CODA_OLLAMA_MODEL=
CODA_OLLAMA_COLD_START_TIMEOUT=120

# Shared LLM timeout
CODA_LLM_TIMEOUT=45
```

Blank API-key values above are placeholders only. Add keys only for the cloud
providers you intend to use.

Never commit real API keys, private provider credentials or private
environment-specific endpoints.

For the complete environment baseline, see [`.env.example`](.env.example).

## Related Documentation

- [Privacy Routing](privacy-routing.md)
- [Main Program Flow](main-program-flow.md)
- [Concurrent Runtime](concurrent-runtime.md)
- [Intent Routing](intent-routing.md)
