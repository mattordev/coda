# C.O.D.A v1.1.0 Release Notes

Release date: 2026-04-15

v1.1.0 is a major quality and capability upgrade focused on local-first voice operation, smarter conversational fallback, a new live dashboard, and safer self-updates.

## Highlights

### Speech stack upgrade: faster local-first, safer fallback

- Added local Whisper transcription support via `faster-whisper`.
- Added provider selection through `CODA_STT_PROVIDER` (`auto` or `google`).
- In `auto` mode, C.O.D.A prefers local Whisper and gracefully falls back to Google recognition.
- Added runtime protections so failed local STT paths are disabled for the rest of the session instead of repeatedly retrying slow/broken startup paths.
- Added practical speech tuning controls:
    - `CODA_WHISPER_MODEL`, `CODA_WHISPER_LANGUAGE`
    - `CODA_PAUSE_THRESHOLD`, `CODA_PHRASE_TIME_LIMIT`
    - `CODA_LISTEN_TIMEOUT`, `CODA_CALIBRATION_SECONDS`
    - optional advanced Whisper controls (`CODA_WHISPER_DEVICE`, `CODA_WHISPER_COMPUTE_TYPE`, `CODA_WHISPER_BEAM_SIZE`, `CODA_WHISPER_VAD_FILTER`)

### Better conversations after wakeword

- Added follow-up conversation windows so users can continue speaking without repeating the wakeword each turn.
- Added `CODA_FOLLOWUP_TIMEOUT` to control follow-up window duration.
- Added follow-up stop phrase handling (`stop`, `cancel`, `never mind`, and similar phrases).

### LLM fallback is now more flexible and resilient

- Added multi-provider support with `CODA_LLM_PROVIDER` (`openai` or `ollama`).
- Added OpenAI model selection via `CODA_OPENAI_MODEL`.
- Added Ollama host/model configuration via `CODA_OLLAMA_BASE_URL` and `CODA_OLLAMA_MODEL`.
- Added automatic Ollama model selection when model is not configured (with preferred model fallback).
- Improved response reliability with retry behavior for empty fallback responses.
- Added conversation history controls and trimming behavior via `CODA_CONVERSATION_MAX_TURNS`.
- Added timeout control via `CODA_LLM_TIMEOUT`.
- Added `CODA_SYSTEM_PROMPT` override support for assistant behavior customization.

# C.O.D.A v1.1.0

Released: 2026-04-15

v1.1.0 is a major platform update for C.O.D.A, focused on faster local voice recognition, better conversational flow, a live runtime dashboard, and a safer updater pipeline.

## Release Highlights

### Local-first speech recognition, with graceful fallback

- Local Whisper transcription support added via `faster-whisper`.
- Configurable provider routing with `CODA_STT_PROVIDER` (`auto` or `google`).
- In `auto` mode, C.O.D.A prefers local Whisper and falls back cleanly to Google when needed.
- Runtime guardrails now prevent repeatedly retrying failed local STT paths within the same session.
- New tuning controls for speech behavior and capture quality:
    - `CODA_WHISPER_MODEL`, `CODA_WHISPER_LANGUAGE`
    - `CODA_PAUSE_THRESHOLD`, `CODA_PHRASE_TIME_LIMIT`
    - `CODA_LISTEN_TIMEOUT`, `CODA_CALIBRATION_SECONDS`
    - optional advanced controls: `CODA_WHISPER_DEVICE`, `CODA_WHISPER_COMPUTE_TYPE`, `CODA_WHISPER_BEAM_SIZE`, `CODA_WHISPER_VAD_FILTER`

### More natural follow-up conversations

- Follow-up windows now allow multi-turn voice conversations without repeating the wakeword every turn.
- `CODA_FOLLOWUP_TIMEOUT` added to control follow-up duration.
- Voice stop phrases are supported (`stop`, `cancel`, `never mind`, and similar phrases).

### Smarter and more configurable LLM fallback

- Provider abstraction added via `CODA_LLM_PROVIDER` (`openai` or `ollama`).
- OpenAI model selection through `CODA_OPENAI_MODEL`.
- Ollama host/model support through `CODA_OLLAMA_BASE_URL` and `CODA_OLLAMA_MODEL`.
- Automatic Ollama model selection when no explicit model is provided (with preferred model fallback).
- Empty fallback responses now retry for improved continuity.
- Conversation memory now respects `CODA_CONVERSATION_MAX_TURNS` with safer trimming.
- Timeout management added through `CODA_LLM_TIMEOUT`.
- Assistant behavior can now be customized with `CODA_SYSTEM_PROMPT`.

### New live dashboard experience

- Flask-backed live dashboard added with runtime state snapshots via `/api/state`.
- Heartbeat tracking added for runtime liveness visibility.
- Rolling user and assistant transcript panels added.
- AI motion/event state added for richer UI feedback.
- Thread-safe dashboard persistence added in `dashboard_state.json`.

### Operational improvements

- CLI microphone controls added: `--mic`, `--mic-index`, `--list-mics`.
- Manual/voice mode transitions and voice thread lifecycle behavior improved.
- Command cache validation improved with automatic rebuilds when command files change.

### Updater hardening and reliability

- Zip extraction hardened against Zip Slip path traversal.
- Update staging and activation flow improved with stronger validation.
- Local runtime files now preserved during update (`.env`, API key files, `wakewords.json`, `commands.json`).
- Local env/cache/build directories excluded from backups for faster updates.
- Best-effort rollback path added for failed activations.

## Fixes Included

- Fixed conversation trimming to consistently honor configurable turn limits.
- Fixed empty-response fallback behavior for better assistant continuity.
- Refined runtime flow and docs comments for clarity.

## Compatibility Notes

- `CODA_GPT_FALLBACK` remains supported as a legacy alias for `CODA_LLM_FALLBACK`.
- Legacy key files remain supported: `ELapi_key.txt`, `ELapikey.txt`.
- Full environment setup guidance is available in `README.md`.

## Upgrade Checklist

1. Update to v1.1.0.
2. Review `.env` and set `CODA_STT_PROVIDER` and `CODA_LLM_PROVIDER`.
3. If using Ollama, set `CODA_OLLAMA_BASE_URL` and optionally `CODA_OLLAMA_MODEL`.
4. Tune voice behavior with pause, phrase, and follow-up timeout settings as needed.
5. Verify runtime state on the dashboard endpoint at `/dashboard`.

## Closing

Thank you to everyone testing voice processing, dashboard state flow, and updater reliability. v1.1.0 establishes a stronger foundation for future command depth and local/offline assistant capability.
