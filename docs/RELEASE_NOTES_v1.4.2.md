# C.O.D.A v1.4.2 — Local-First TTS

C.O.D.A v1.4.2 introduces high-quality local speech through Pocket TTS,
making offline, privacy-conscious text-to-speech a practical first-class
option.

## Highlights

- Added Pocket TTS as a CPU-local speech provider.
- Added configurable TTS provider ordering and fallback.
- Retained ElevenLabs as an optional cloud provider.
- Retained pyttsx3 as the emergency local fallback.
- Streamed Pocket audio through interruptible `ffplay` playback.
- Added reusable provider instances for faster warm responses.
- Added provider-independent pronunciation of versions, decimals,
  percentages, units, dates, times, currency, and common initialisms.
- Preserved original text for display and conversation history.
- Kept manual input responsive while speech is playing.
- Added manual speech cancellation using commands such as `coda stop`.
- Prevented cancellation from triggering fallback or leaking stale audio.
- Prevented clipping at the end of Pocket speech.
- Split oversized input safely below Pocket's 50-token limit.
- Corrected llama.cpp UTF-8 stream decoding.
- Expanded provider diagnostics, configuration reload support, tests,
  documentation, privacy guidance, and troubleshooting.

## Dependency update

Pocket TTS now runs in C.O.D.A's main environment using the validated NumPy 2
and Pydantic 2 dependency migration. The release pins Pocket TTS and its CPU
inference dependencies for reproducible installation.

Pocket's model assets are downloaded on first use and can operate from the
local cache afterward. `ffplay` is required for Pocket and ElevenLabs
playback.

## Validation

- Clean Windows Python 3.11 installation passed.
- Dependency integrity check passed.
- Full automated suite passed with 375 tests.
- Live playback, long-text generation, cancellation, repeated reuse,
  pronunciation, configuration reload, diagnostics, and manual-mode
  responsiveness were validated.
- No model files, generated audio, credentials, local endpoints, or
  machine-specific paths are included.
