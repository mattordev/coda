# Optional model pre-warming

Pre-warming prepares configured local models in the background after command/config
setup and the updater handshake. It is disabled by default. Set independent choices
in the effective environment or `.env`:

```dotenv
CODA_PREWARM_LLM=true
CODA_PREWARM_TTS=false
CODA_PREWARM_STT=false
```

`--prewarm-llm` and `--no-prewarm-llm` override the environment for that launch.
Equivalent `--prewarm-tts`/`--no-prewarm-tts` and `--prewarm-stt`/`--no-prewarm-stt`
flags are available. If opposite flags occur, the last explicit choice wins.
Environment values `1`, `true`, `yes`, `on` enable; other values disable.
Existing `.env` loading rules apply; no second model-name setting is introduced.

## What gets prepared

- **LLM:** the same first configured local provider/model used by the intent
  classifier (`CODA_LOCAL_PROVIDERS`), when classification is enabled. The built-in
  Ollama and llama.cpp adapters send a bounded, one-token synthetic request to
  their configured server. No user data, history, tool results, cloud fallback or
  remote commercial API is involved. This is server-side model preparation, not
  a second in-process LLM. The existing server owns model residency/eviction.
- **TTS:** PocketTTS when present in `TTS_PROVIDER_ORDER`, using the same retained
  language/voice runtime as speech. It requires the normal `ffplay` eligibility.
  It generates/plays no audio. Cloud TTS and the OS pyttsx3 engine are not warmed.
- **STT:** faster-whisper when selected by normal `CODA_STT_PROVIDER` resolution.
  It follows the existing model/device/compute-type and fallback order, retaining
  the first successful model. Google STT is skipped; no microphone input is taken.

The initial provider selection is deliberately narrow: one local LLM, PocketTTS,
solo Whisper runtime success. Use subsystem toggles to avoid warming an infrequently
used local fallback. No separate provider-order configuration is needed.

Background loads are serialized and recheck available host RAM before each task.
`CODA_PREWARM_MIN_AVAILABLE_MB` defaults to 1024; a nonnegative integer changes the
minimum free RAM guard (0 disables this guard). Invalid/negative values skip loads.
This is a free-memory threshold, not a model-size estimate or hard memory limit.
Whisper's existing hardware selection still applies. No new GPU allocation policy
is imposed; users must choose model/device settings appropriate to their hardware.

## Reuse, status and shutdown

`debug status` reports queued/warming/ready/failed/skipped and duration. The public
`runtime.model_warmup.snapshot()` additionally contains process RSS and, when
PyTorch CUDA is already initialized, allocated VRAM before/after preparation.
Unknown measurements are `null`. These process-wide snapshots include concurrent
activity and do not measure external Ollama/llama.cpp memory. They are observations,
not per-model attribution. Errors are safe and also printed when warming fails.

With `CODA_DEBUG=true`, `[MODEL]` messages show preparation starting, completion,
failure or cancellation, and elapsed time every five seconds while waiting.
PocketTTS and faster-whisper also report on-demand cache misses; cached reuse is
silent. PocketTTS reports weight loading and voice preparation as separate stages.
LLM requests report waiting for the server and the first server event. That wait
may include model loading, queueing and prompt processing; it is not proof of a
cold model. No additional server probes or inference requests are made.

Elapsed time is activity feedback, not a percentage or estimated time remaining.
The current native loaders do not provide reliable percentage callbacks. Debug
output contains stage names rather than prompts, model paths or exception bodies.

Native commands and unrelated providers continue while warming. A first request
for the selected LLM waits on its in-flight preparation, with cancellation support.
PocketTTS and Whisper first use share their normal initialization lock/cache with
warming: no duplicate local model is constructed. Warm-up failure does not crash
startup, replay a user action, or disable ordinary provider fallback.

`debug reload` cancels/retire the old warming generation before resetting provider
state, then applies the effective configuration and schedules new optional tasks.
Reload does not synchronously probe/load TTS. Shutdown waits for a bounded interval,
marks model owners closed, and clears retained state. Native loaders/downloads may
not be interruptible: late results are discarded and cannot repopulate a retired
cache or publish ready state. Their worker may finish after shutdown. Initialization
is serialized across replacement model owners to avoid overlapping native loads.
Active requests retain their own references until they finish/cancel safely.

Model preparation may download model weights through the providers' existing loaders
and can consume significant RAM/VRAM. Enable only the subsystems you intend to use.
It removes some first-use loading/initialization cost; it does not promise faster
steady-state inference or prevent an external server from unloading a warm model.

## Measure cold versus warm

Use the same configured model and request, with external LLM residency reset between
cold comparisons. Start once with `--no-prewarm-llm`, measure the first natural-language
classification and a repeat, then restart with `--prewarm-llm`, wait for `debug status`
to show ready, and repeat. Compare first-request latency separately from startup
preparation duration. Repeat similarly for speech/STT while holding model and device
constant. Inspect memory snapshots and operating-system/server memory separately.

Automated tests use controlled fake loaders and local disposable HTTP responses;
no live models, audio, downloads or user data are needed. Deterministic timing tests
prove first use reuses preparation, rather than asserting machine-dependent speed.
