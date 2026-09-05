# Local text-to-speech

CODA's supported local speech path uses Pocket TTS for neural speech and
`pyttsx3` as the final operating-system fallback. ElevenLabs remains available
as an optional cloud provider.

```text
Optional cloud speech: ElevenLabs
Local neural speech:   Pocket TTS
Emergency fallback:   pyttsx3
```

Providers use one common session boundary, so ordering, fallback and
cancellation are controlled by CODA rather than by provider-specific routing
code.

## Supported environment

CODA supports this integration on the platforms covered by its automated test
matrix:

- Windows with Python 3.11 or 3.12.
- Ubuntu Linux with Python 3.11 or 3.12.

Pocket TTS runs on CPU; a CUDA installation is not required. macOS, Raspberry
Pi hardware and live audio devices are not currently part of CODA's validation
matrix.

Use CODA's project virtual environment. Pocket TTS is pinned in
`requirements.in` and `requirements.txt` alongside a CPU build of PyTorch, so
the old `.venv-pocket-tts` research environment is neither required nor used by
the application.

## Installation

Pocket TTS and ElevenLabs play audio through `ffplay`, which is supplied by
[FFmpeg](https://ffmpeg.org/download.html). Pocket streams PCM frames to it;
ElevenLabs plays a completed MP3 response. Install FFmpeg and make sure
`ffplay` is on `PATH` before starting CODA:

```text
ffplay -version
```

On Ubuntu, install the audio prerequisites first:

```bash
sudo apt-get update
sudo apt-get install ffmpeg portaudio19-dev libespeak1 flac
```

`ffmpeg` supplies `ffplay`. PortAudio and FLAC support voice capture and speech
recognition rather than Pocket TTS generation itself. `libespeak1` supplies the
Linux speech engine used by the pinned `pyttsx3` emergency fallback.

Create a clean project environment on Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Or on Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy `docs/.env.example` to `.env` and configure the desired providers. Do not
install Pocket TTS into a separate environment: CODA must import it from the
same interpreter used to run `main.py`.

## Configuration

The local-first baseline is:

```env
TTS_PROVIDER_ORDER=pockettts,pyttsx3
CODA_POCKET_TTS_LANGUAGE=english
CODA_POCKET_TTS_VOICE=alba
```

`TTS_PROVIDER_ORDER` is evaluated from left to right. Providers may be
reordered or omitted without changing code. Names are case-insensitive,
duplicate entries are ignored after their first occurrence, and unknown names
are skipped with a debug message.

To prefer ElevenLabs when its API key and network connection are available:

```env
TTS_PROVIDER_ORDER=elevenlabs,pockettts,pyttsx3
ELEVENLABS_API_KEY=
```

Leaving `ELEVENLABS_API_KEY` blank makes that provider unconfigured, so CODA
continues locally. Omitting `elevenlabs` from the order disables it even when a
key exists. If `TTS_PROVIDER_ORDER` itself is absent or blank, CODA's built-in
order is `elevenlabs,pockettts,pyttsx3`.

`CODA_POCKET_TTS_LANGUAGE` selects the retained Pocket language model. English
is CODA's supported and tested default. `CODA_POCKET_TTS_VOICE` accepts a
built-in voice name, an exported voice-state file or a local audio prompt. A
blank value asks Pocket TTS for the selected language's default voice; the
example selects Alba explicitly.

Built-in voices and compatible precomputed states work with Pocket's anonymous
baseline. Using a raw audio prompt for voice cloning requires accepting the
terms for the gated `kyutai/pocket-tts` repository and authenticating with
Hugging Face. Without that access, Pocket 3.0.2 falls back to
`kyutai/pocket-tts-without-voice-cloning`; ordinary speech still works, but a
raw audio prompt does not.

After changing TTS settings while CODA is running, use:

```text
coda debug reload
```

Reload closes the retained Pocket provider, reloads `.env`, and creates a new
provider from the updated language, voice and ordering settings. It immediately
runs an availability check, so the reload command itself may load or download
the newly selected assets. Restart CODA if a change modifies the Python
environment, `PATH` or `HF_HOME`.

## Downloads, cache and offline operation

The first Pocket availability check loads the model and selected voice. Missing
assets are downloaded through Hugging Face and placed in its normal local
cache. `HF_HOME` can be set before starting CODA when that cache needs to live
somewhere else. `HF_TOKEN` is optional for the built-in anonymous baseline. It
is used for gated voice cloning and can also raise Hub rate limits; never
commit a populated token.

After every required model, tokenizer and voice asset has been cached, Pocket
TTS generation and playback remain local and can operate without network
access. Selecting an uncached language or remote voice asset while offline
makes Pocket unavailable for that provider lifetime, and CODA proceeds to the
next configured provider. Run `debug reload` after restoring the assets or
network.

The Windows research system observed approximately 209 MB for the Pocket model
cache plus tokenizer/supporting files, and approximately 6.2 MB for the tested
voice state. Cache contents and sizes can change between models, languages,
voices and upstream releases. The Python virtual environment is separate from
the Hugging Face asset cache and requires additional disk space.

Pocket generation never sends response text to an external TTS service. Audio
frames remain on the machine and pass to a local `ffplay` process. First-use
asset downloads still contact Hugging Face, but do not include the response
being spoken. ElevenLabs is different: selecting it sends spoken text to its
cloud API. `pyttsx3` uses the local operating-system speech engine.

## Licences and voices

- The [Pocket TTS Python implementation](https://github.com/kyutai-labs/pocket-tts)
  is MIT licensed.
- The gated [voice-cloning weights](https://huggingface.co/kyutai/pocket-tts)
  and anonymous
  [non-cloning weights](https://huggingface.co/kyutai/pocket-tts-without-voice-cloning)
  are published under CC BY 4.0. Their model cards also state prohibited-use
  conditions.
- The [Alba MacKenna voice recordings](https://huggingface.co/kyutai/tts-voices)
  are CC BY 4.0.
- Other upstream voices have their own licences, including CC0, CC BY 4.0 and
  non-commercial licences. Check the voice repository before selecting or
  redistributing one. Users supplying a custom voice are responsible for
  having permission to use it.

CODA does not redistribute the model or voice assets; Pocket downloads the
selected files into the user's cache. Attribution details are also recorded in
`THIRD_PARTY_NOTICES.md`.

## Streaming, fallback and cancellation

CODA normalises a separate spoken copy of each response, then divides it at
safe sentence boundaries. The displayed response and conversation history are
not modified.

Pocket generates 24 kHz mono float32 PCM frames incrementally. CODA writes each
frame to `ffplay` as it arrives instead of waiting for a complete waveform. A
short silence tail lets the playback device drain without clipping the final
phoneme. If a provider genuinely fails, CODA attempts the next provider for
only the incomplete sentence. Sentences already played are not repeated, and
a successful fallback remains selected for the rest of that response.

Cancellation has deliberately different semantics:

- Active `ffplay` playback is terminated immediately.
- Cancellation never advances to another provider and never replays speech.
- Pocket TTS does not expose a guaranteed synthesis-cancellation primitive.
- CODA therefore drains and discards remaining generated frames while holding
  the retained model's generation lock.
- The next Pocket request starts only after that abandoned generation is fully
  drained, preventing stale audio or unsafe concurrent model access.

This means audible interruption is prompt, but background CPU work can continue
briefly. A later request may wait for that cleanup before it begins speaking.

## Status and diagnostics

`coda coda status` reports Text-To-Speech as `Available` when at least one
configured provider passes its availability check, otherwise `Unavailable`.
This is aggregate status; it does not claim that every configured provider is
ready.

Use `coda debug on` to expose provider-level diagnostics. Relevant messages
identify:

- Unknown or unconfigured providers being skipped.
- Resolver and availability errors.
- Pocket model or voice initialisation failure.
- Playback or generation failure and the provider attempted next.
- The fallback provider that completed the sentence.
- A final `[RUNTIME] Speech failed` event if the configured chain is exhausted.

The Pocket model runs in CODA's process. There is no separate Pocket model
worker to launch. A dedicated speech thread owns queued tasks, while each
Pocket sentence owns a short-lived `ffplay` child process. An unexpected task
failure is reported as a failed worker event; the speech thread remains ready
for the next queued task.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `No module named 'pocket_tts'` | Activate CODA's `.venv`, install `requirements.txt`, and run CODA with that environment's Python. |
| `ffplay is not available` | Install FFmpeg, confirm `ffplay -version` works in the same terminal, then restart CODA. |
| Model or voice cannot be downloaded | Confirm the first run has network access, the Hugging Face cache is writable, free disk space is available, and any required Hub terms have been accepted. An `HF_TOKEN` may help with authentication or rate limits. |
| `initialization previously failed` | Fix the underlying dependency, asset or cache error, then use `coda debug reload` or restart CODA. The failure is cached to avoid repeating an expensive broken load on every response. |
| Windows Hugging Face symlink warning | Enable Windows Developer Mode or accept the less space-efficient cache. `HF_HUB_DISABLE_SYMLINKS_WARNING=1` hides only the warning; it does not fix a failed download. |
| Playback starts but generation fails | Enable debug output. CODA stops that session and attempts the next provider for the incomplete sentence. Check RAM, cache integrity and the original exception. |
| `ffplay` exits or its input pipe closes | Verify FFmpeg independently and check that security software is not terminating the child process. CODA drains Pocket output before falling through. |
| `pyttsx3` reports a missing `libespeak.so.1` | Install Ubuntu's `libespeak1` package, then restart CODA. |
| The speech worker does not process queued responses | Restart CODA and enable debug output. Confirm the main process remains running and use the focused test command below to distinguish a worker regression from provider or audio-hardware failure. Pocket has no separate service to start. |
| Speech stops but the next request is briefly delayed | Pocket synthesis is being drained after audible cancellation. This prevents old frames reaching the next response. |
| All providers fail | Check `TTS_PROVIDER_ORDER`; keep `pyttsx3` last when an operating-system voice is available. The text response remains visible even when speech is unavailable. |

## Observed benchmark

These figures are observations from the research machine, not minimum
requirements or platform-wide guarantees. Testing used Windows, Python 3.11,
CPU-only PyTorch and an AMD Ryzen 7 3700X (8 cores/16 threads).

| Measurement | Observed result |
| --- | --- |
| Cached model load | Approximately 0.65-0.67 seconds |
| Voice-state load | Approximately 2 milliseconds |
| Time to first audio | Approximately 199-239 milliseconds |
| Audio frame duration | Approximately 80 milliseconds |
| Generation speed | Approximately 2.3-3x realtime |
| CPU use during repeat streaming | Approximately 1.5-1.9 cores |
| Ready memory | Approximately 697 MB |
| Post-generation memory | Approximately 776-854 MB |
| Peak working set | Approximately 906 MB |
| Audible playback cancellation | Approximately 4-7 milliseconds |

Ten consecutive retained-model generations completed, and a normal generation
also completed after three cancellation cycles. Allocator/cache behaviour and
performance should be re-measured on each target system.

## Automated tests

The normal suite uses fake runtimes, PCM frames, playback processes, providers
and environment settings. It does not require credentials, network access,
model downloads, audio hardware or a developer `.env`.

Run the focused TTS coverage with:

```text
python -m unittest tests.test_tts_registry tests.test_speech_provider_resolution tests.test_pockettts_provider tests.test_speech_playback tests.test_speech_worker tests.test_spoken_text tests.test_speech_segmentation -v
```

Live audio quality and timing remain part of release validation rather than the
deterministic unit suite.

## Future quality tier

Kyutai TTS 1.6B is tracked separately in issue #111 as a possible opt-in GPU
quality tier. It is not installed, selected or required by v1.4.2. Hardware
eligibility, GPU contention, warm-up and hard process-level cancellation remain
future work and cannot block the Pocket TTS release.
