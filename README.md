# C.O.D.A Source Code

Primary runtime entry point is _main.py_.

## What does this program do?

C.O.D.A is a local-first smart assistant with wake word detection, command modules, LLM fallback chat, text-to-speech, and a live dashboard. It is designed to run on local hardware including Raspberry Pi setups, with graceful online and offline behavior depending on configured providers.

C.O.D.A stands for **Cognitive Operational Data Assistant**, but this project was originally called H.A.D.E.S, aka Home And Data... something or other. This project started in 2016, but was forgotten and not worked on for many years. The initial concept was for this system/assistant to act as a user interface and aid in general purpose tasks.

Now, there's not much "cognitive" about a basic smart assistant. But later down the line, depending on the completion of the first prototype and what I manage to get done I also want to look at Machine Learning for more accurate wakeword detection and speech synthesis.

C.O.D.A has several planned commands and features, the planned commands and finished commands are here:

Planned:

- Check system status (Temperature, storage, etc)
- Create "project" folders & files
- Spotify integration (so you can play your best tunes, whilst you do your best work)
- Search the systems default browser for a query
- Home automation (Change thermostat, lock doors, manage other home based sensors)

Finished:

- Check for an internet connection

Other features:

- Flask-based dashboard for live runtime state and transcript visibility
- System logging and runtime diagnostics

---

### Installation

Install prerequisites first:

Prereqs for pyaudio and speech recognition:
`sudo apt-get install libportaudio0 libportaudio2 libportaudiocpp0 portaudio19-dev flac`

After cloning, install Python dependencies from the project root:
`pip install -r requirements.txt`

### Run

Start the assistant:
`python main.py`

Start the dashboard web app:
`python dashboard/app.py`

Open the dashboard in your browser:
`http://localhost:5000/dashboard`

Speech-to-text notes:

- CODA now prefers local Whisper transcription through `faster-whisper` when available.
- Google speech recognition is still supported as a fallback.
- The first Whisper run will download the selected model.
- `faster-whisper` uses bundled FFmpeg libraries via PyAV, so you do not need a separate system `ffmpeg` install for the local Whisper path.

### Environment Variables

Copy `.env.example` to the project root (located in /docs), rename it to `.env` and populate the values appropriate for your setup.

### Provider Routing

CODA uses privacy-aware provider routing:

- High-risk requests (for example passwords, bank details, API keys) are processed using local providers only by default.
- Medium-risk requests attempt local providers first, then fall back to cloud providers with sensitive values sanitised.
- Low-risk requests attempt cloud providers first, then fall back to local providers.
- Local providers receive raw conversation history. Cloud providers receive the cloud-safe rendering of history, which can be raw, sanitised, summarized, or blocked depending on privacy policy.

Providers are attempted in the order specified in `.env` using:

```text
CODA_CLOUD_PROVIDERS=openai
CODA_LOCAL_PROVIDERS=ollama
```

Provider names are resolved through `ai/providers/registry.py`, which defines provider type, configuration requirements, and the implementation module. Use the canonical provider names from the registry. `CODA_CLOUD_PROVIDERS` only accepts cloud providers, and `CODA_LOCAL_PROVIDERS` only accepts local providers. Unavailable providers are automatically skipped and temporarily placed into cooldown before being retried.

Privacy behavior is controlled with:

```text
CODA_PRIVACY_MODE=balanced
CODA_CLOUD_PRIVACY_ACTION=sanitize
CODA_HIGH_RISK_CLOUD_FALLBACK=block
CODA_PRIVACY_LOW_RISK_THRESHOLD=0.3
CODA_PRIVACY_HIGH_RISK_THRESHOLD=0.7
```

Modes are `strict`, `balanced`, and `permissive`. See `docs/privacy-routing.md` for the full routing and cloud-safe rendering rules.

Notes:

- `ELEVENLABS_API_KEY` is required for ElevenLabs TTS auth.
- `CODA_LLM_FALLBACK=1` enables LLM fallback when no command word is detected after the wakeword.
- If `CODA_OLLAMA_MODEL` is omitted, CODA prefers `nemotron-3-nano:4b` when that model exists and otherwise falls back to the first model returned by Ollama's `/api/tags` endpoint.
- `CODA_OLLAMA_COLD_START_TIMEOUT` controls the longer timeout used only when an Ollama model is not already loaded.
- `CODA_PRIVACY_MODE` controls whether sensitive requests are blocked from cloud providers or sent with sanitised/summarized context.
- LLM voice replies open a short follow-up window so the next spoken reply can skip the wake word. Adjust this with `CODA_FOLLOWUP_TIMEOUT` in seconds.
- `CODA_STT_PROVIDER=auto` prefers local Whisper via `faster-whisper`, then falls back to Google recognition.
- In `auto` mode, CODA will also step down to a more compatible local Whisper setup before using Google, for example when CUDA is detected but the local CUDA runtime is not actually usable.
- If a local Whisper attempt fails at runtime, CODA disables that exact attempt for the rest of the session so later utterances do not keep paying the same startup penalty. `debug reload` clears that session cache.
- `CODA_STT_PROVIDER=google` keeps the Google-only speech recognition path.
- `CODA_WHISPER_MODEL=auto` picks a hardware-friendly default: `turbo` on CUDA systems, `small` on desktop CPU, and `base` on ARM boards such as Raspberry Pi.
- `CODA_WHISPER_LANGUAGE` is optional. Leave it unset for auto-detection, or set it to `en` for English-first command recognition.
- `CODA_PAUSE_THRESHOLD` controls how long a spoken pause CODA tolerates before it treats the utterance as finished. The default is now `1.2` seconds.
- `CODA_PHRASE_TIME_LIMIT` caps the maximum length of a single captured utterance. The default is now `12` seconds.
- Optional Whisper tuning: `CODA_WHISPER_DEVICE`, `CODA_WHISPER_COMPUTE_TYPE`, `CODA_WHISPER_BEAM_SIZE`, and `CODA_WHISPER_VAD_FILTER`.
- `CODA_SYSTEM_PROMPT` lets you override CODA's default assistant style without editing code. Run `debug reload` after changing it.

### Command Development

Commands are self-registering Python modules that own their intent metadata
and execution function. See [Command Modules](docs/command-modules.md) for the
drop-in template and validation rules.

---

#### Licence

GNU AGPLv3

Copyright (C) 2022 Matthew Roberts

To view the full license, please view `LICENCE.md`

