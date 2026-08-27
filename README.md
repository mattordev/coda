# C.O.D.A

[![Tests](https://github.com/mattordev/coda/actions/workflows/tests.yml/badge.svg)](https://github.com/mattordev/coda/actions/workflows/tests.yml)
[![Pylint](https://github.com/mattordev/coda/actions/workflows/pylint.yml/badge.svg)](https://github.com/mattordev/coda/actions/workflows/pylint.yml)

C.O.D.A is a local-first voice assistant with wake-word detection, drop-in
command modules, intent routing, LLM fallback, text-to-speech and a live
dashboard.

C.O.D.A stands for **Cognitive Operational Data Assistant**. The project began
in 2016 under the name H.A.D.E.S, was left alone for several years, and was
eventually revived as the assistant it is today.

## What It Can Do

- Accept wake-word voice input or typed input in manual mode.
- Route exact commands, aliases and natural-language examples through the
  intent system.
- Fall back to a local or cloud LLM when no command matches.
- Use ordered fallback across multiple local and cloud LLM providers.
- Keep sensitive requests local or sanitise them before cloud fallback.
- Prefer local Whisper speech recognition with Google as a fallback.
- Speak responses through ElevenLabs with a local `pyttsx3` fallback.
- Load self-registering commands from the `commands` directory.
- Report live runtime state and transcripts through the dashboard.

### Commands

| Command     | What it does                                 | Example                           |
| ----------- | -------------------------------------------- | --------------------------------- |
| `connected` | Checks whether CODA can reach the internet.  | `coda are we connected?`          |
| `debug`     | Views or changes runtime debugging settings. | `coda debug status`               |
| `maps`      | Opens a Google Maps search.                  | `coda where is Victoria Station?` |
| `say`       | Speaks the supplied text.                    | `coda say hello there`            |
| `status`    | Reports CODA or system information.          | `coda system status`              |
| `time`      | Reports the local time or date.              | `coda what time is it?`           |

## Roadmap

The active roadmap is maintained in the
[CODA GitHub project](https://github.com/users/mattordev/projects/1/views/1).

### Completed Milestones

- [v1.2.0 - Provider Routing & Privacy](https://github.com/mattordev/coda/milestone/1)
- [v1.3.0 - Intent Routing & Command System](https://github.com/mattordev/coda/milestone/2)
- [v1.4.0 - Concurrent Runtime & Interruptible Responses](https://github.com/mattordev/coda/milestone/3)

### Future Milestones

- [v1.4.5 - Provider Telemetry & Metrics](https://github.com/mattordev/coda/milestone/7)
- [v1.5.0 - MCP & External Tool Integration](https://github.com/mattordev/coda/milestone/4)
- [v1.6.0 - Semantic Intent Matching](https://github.com/mattordev/coda/milestone/5)

## Supported Platforms

CODA is currently tested on:

- Windows with Python 3.11 and 3.12
- Linux with Python 3.11 and 3.12

Automated tests run against Windows and Ubuntu through GitHub Actions.

Known limitations:

- The global `Ctrl+B` hotkey may require additional permissions on Linux and
  may not work under WSL, containers, headless systems or some Wayland
  sessions.
- Manual mode remains available with `python main.py -m` when the global hotkey
  is unavailable.
- macOS and Raspberry Pi hardware are not currently included in automated
  testing.
- Audio and microphone hardware are not exercised by CI.

## Installation

CODA requires Python 3.11 or 3.12.

Create and activate a virtual environment, then install the locked dependencies:

```text
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Ubuntu, install the audio prerequisites first:

```text
sudo apt-get update
sudo apt-get install libportaudio0 libportaudio2 libportaudiocpp0 portaudio19-dev flac
```

Copy `docs/.env.example` to `.env` in the project root and fill in the settings
for the providers you want to use.

PowerShell:

```powershell
Copy-Item docs/.env.example .env
```

Linux:

```bash
cp docs/.env.example .env
```

## Running CODA

Start in voice mode:

```text
python main.py
```

Start in manual mode:

```text
python main.py -m
```

Manual requests still require a wake word, for example:

```text
manual> coda what time is it?
```

Start the dashboard separately:

```text
python dashboard/app.py
```

Then open `http://localhost:5000/dashboard`.

## AI Provider Routing

CODA supports multiple local and cloud LLM providers through a shared provider
registry and routing system.

### Supported Providers

Cloud providers:

- OpenAI
- Google Gemini
- xAI Grok
- OpenRouter

Local providers:

- Ollama
- llama.cpp

Provider order is configured in `.env`.

For example:

```text
CODA_CLOUD_PROVIDERS=openrouter,gemini,grok,openai
CODA_LOCAL_PROVIDERS=llamacpp,ollama
```

Providers are evaluated from left to right within each configured group.

For a low-risk request using the example above, CODA may therefore attempt:

```text
openrouter -> gemini -> grok -> openai -> llamacpp -> ollama
```

If a provider fails, CODA can continue through the remaining eligible providers
until one succeeds or the provider list is exhausted.

Unconfigured or unknown providers are skipped rather than preventing CODA from
starting.

Providers that have recently failed may also enter a temporary cooldown so
repeated requests do not continually wait on a provider that is known to be
unavailable.

### Privacy-Aware Ordering

The privacy router decides whether local or cloud providers should be attempted
first.

By default:

- Low-risk requests try cloud providers before local providers.
- Medium-risk requests try local providers first and sanitise sensitive values
  before a permitted cloud fallback.
- High-risk requests stay local unless the privacy policy explicitly permits a
  cloud fallback.
- Cloud providers receive privacy-safe conversation history rather than
  automatically receiving all raw stored content.
- Local providers can receive the original conversation content.
- Cancelling an active provider request prevents the router from continuing
  fallback for that request.

Privacy behaviour is controlled with:

```text
CODA_PRIVACY_MODE=balanced
CODA_CLOUD_PRIVACY_ACTION=sanitize
CODA_HIGH_RISK_CLOUD_FALLBACK=block
CODA_PRIVACY_LOW_RISK_THRESHOLD=0.3
CODA_PRIVACY_HIGH_RISK_THRESHOLD=0.7
```

See [Privacy Routing](docs/privacy-routing.md) for the complete policy and
cloud-safe conversation rules.

### Provider Configuration

Each provider exposes its configuration through environment variables.

| Provider   | Type  | API key              | Model                   | Base URL                 |
| ---------- | ----- | -------------------- | ----------------------- | ------------------------ |
| OpenAI     | Cloud | `OPENAI_API_KEY`     | `CODA_OPENAI_MODEL`     | Built in                 |
| Gemini     | Cloud | `GEMINI_API_KEY`     | `CODA_GEMINI_MODEL`     | Built in                 |
| Grok       | Cloud | `XAI_API_KEY`        | `CODA_GROK_MODEL`       | Built in                 |
| OpenRouter | Cloud | `OPENROUTER_API_KEY` | `CODA_OPENROUTER_MODEL` | Built in                 |
| Ollama     | Local | Not required         | `CODA_OLLAMA_MODEL`     | `CODA_OLLAMA_BASE_URL`   |
| llama.cpp  | Local | Not required         | `CODA_LLAMACPP_MODEL`   | `CODA_LLAMACPP_BASE_URL` |

The full configuration baseline is available in
[`docs/.env.example`](docs/.env.example).

### Model Selection

OpenAI, Gemini and Grok provide default model values when no custom model is
configured.

For example:

```text
CODA_OPENAI_MODEL=gpt-4o-mini
CODA_GEMINI_MODEL=gemini-3.7-flash
CODA_GROK_MODEL=grok-4.5
```

OpenRouter works slightly differently. CODA does not maintain a hard-coded list
of OpenRouter models. Instead, `CODA_OPENROUTER_MODEL` accepts an arbitrary valid
OpenRouter model ID.

For example:

```text
CODA_OPENROUTER_MODEL=openrouter/free
```

This allows OpenRouter's available model catalogue to change without requiring
CODA itself to be updated simply to recognise another model name.

For local providers, a model can either be supplied explicitly or resolved from
the configured local service.

Ollama configuration:

```text
CODA_OLLAMA_BASE_URL=http://localhost:11434
CODA_OLLAMA_MODEL=
```

If `CODA_OLLAMA_MODEL` is left blank, CODA can inspect the configured Ollama
instance and resolve from the locally available models.

llama.cpp configuration:

```text
CODA_LLAMACPP_BASE_URL=http://localhost:8080
CODA_LLAMACPP_MODEL=
```

If `CODA_LLAMACPP_MODEL` is left blank, CODA attempts to resolve the model
exposed by the configured llama.cpp server.

This supports llama.cpp deployments where the model is selected when starting
the server rather than supplied on every request.

### Provider Fallback

Provider fallback is handled generically by the router rather than through
provider-specific routing rules.

For example, if the configured cloud order is:

```text
CODA_CLOUD_PROVIDERS=openrouter,gemini,grok,openai
```

and OpenRouter is unavailable, CODA may continue with Gemini. If Gemini also
fails, Grok can be attempted next, followed by OpenAI.

If all eligible cloud providers fail and the privacy policy permits local
fallback, CODA can continue through the configured local provider list.

Example debug output:

```text
[DEBUG - ROUTER] Provider order: ['openrouter', 'gemini', 'grok', 'openai', 'llamacpp', 'ollama']
[ROUTER] trying provider openrouter
[ROUTER] openrouter succeeded
```

Cancellation is handled differently from an ordinary provider failure. When an
active request is cancelled, CODA stops that request and does not continue
through the fallback list.

### Provider Diagnostics

Providers expose a description containing the currently configured model where
available.

Examples include:

```text
openai (model: gpt-4o-mini)
gemini (model: gemini-3.7-flash)
grok (model: grok-4.5)
openrouter (model: openrouter/free)
```

Debug routing output also reports the resolved provider order and each attempted
provider, making it possible to see which backend ultimately handled a request.

For more detailed provider setup, ordering, fallback and model-selection
documentation, see [LLM Providers](docs/providers.md).

## Voice Configuration

- `CODA_STT_PROVIDER=auto` prefers local Whisper and falls back to Google speech
  recognition.
- `CODA_WHISPER_MODEL=auto` selects a model based on the available hardware.
- `CODA_WHISPER_LANGUAGE` can be left unset for automatic detection or set to
  `en` for English-first recognition.
- `CODA_PAUSE_THRESHOLD` controls how long a pause is allowed within an
  utterance.
- `CODA_PHRASE_TIME_LIMIT` limits the length of one captured utterance.
- `CODA_FOLLOWUP_TIMEOUT` controls how long an LLM voice follow-up can omit the
  wake word.
- `ELEVENLABS_API_KEY` enables ElevenLabs speech output.
- `CODA_ELEVENLABS_TIMEOUT` bounds audio generation before CODA falls back to
  another speech provider (default: 30 seconds).
- `TTS_PROVIDER_ORDER` sets the ordered speech fallback chain. The default is
  `elevenlabs,pockettts,pyttsx3`.
- `CODA_POCKET_TTS_LANGUAGE` selects PocketTTS's retained language model and
  defaults to `english`.
- `CODA_POCKET_TTS_VOICE` selects a built-in voice, local audio prompt or
  exported voice state. Leaving it blank uses the language model's default
  voice (Alba for English).

PocketTTS runs on CPU and streams speech as it is generated. Its model and
selected voice are loaded once, then reused until `debug reload` or application
shutdown. The first use downloads the required model and voice assets; after
they are cached, PocketTTS can operate without a network connection. Playback
stops immediately when cancelled while remaining synthesis output is discarded
before the next response begins.

The remaining provider, privacy, speech and model options are documented in
[`docs/.env.example`](docs/.env.example).

## Development

Run the complete unit-test suite with:

```text
python -m unittest discover -s tests -p "test_*.py" -v
```

Commands are self-registering modules that own their intent metadata and
`run(request)` function. See:

- [Command Modules](docs/command-modules.md)
- [Intent Routing](docs/intent-routing.md)
- [Main Program Flow](docs/main-program-flow.md)
- [Concurrent Runtime](docs/concurrent-runtime.md)
- [LLM Providers](docs/providers.md)
- [Privacy Routing](docs/privacy-routing.md)

## Licence

CODA is licensed under the GNU AGPLv3. See [LICENCE.md](LICENCE.md).

Third-party notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Copyright (C) 2022-2026 Matthew Roberts (mattordev)
