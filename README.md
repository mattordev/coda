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
- Keep sensitive requests local or sanitise them before cloud fallback.
- Prefer local Whisper speech recognition with Google as a fallback.
- Speak responses through ElevenLabs with a local `pyttsx3` fallback.
- Load self-registering commands from the `commands` directory.
- Report live runtime state and transcripts through the dashboard.

### Commands

| Command | What it does | Example |
| --- | --- | --- |
| `connected` | Checks whether CODA can reach the internet. | `coda are we connected?` |
| `debug` | Views or changes runtime debugging settings. | `coda debug status` |
| `maps` | Opens a Google Maps search. | `coda where is Victoria Station?` |
| `say` | Speaks the supplied text. | `coda say hello there` |
| `status` | Reports CODA or system information. | `coda system status` |
| `time` | Reports the local time or date. | `coda what time is it?` |

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

Provider order is configured in `.env`:

```text
CODA_CLOUD_PROVIDERS=openai
CODA_LOCAL_PROVIDERS=ollama
```

By default:

- Low-risk requests try cloud providers before local providers.
- Medium-risk requests try local providers first and sanitise sensitive values
  before a cloud fallback.
- High-risk requests stay local unless the privacy policy explicitly permits a
  cloud fallback.
- Unavailable providers enter a temporary cooldown before CODA retries them.

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

## Licence

CODA is licensed under the GNU AGPLv3. See [LICENCE.md](LICENCE.md).

Third-party notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Copyright (C) 2022-2026 Matthew Roberts (mattordev)
