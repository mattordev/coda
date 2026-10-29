# v1.5.3 speech latency experiment

Measured on 29 October 2026 with source
`c391ff5c7781ad57dd83d7f4efb61c7dc7e2a49f`, using the production provider
stream parser, bounded channel, speech accumulator/normalizer, speech processor
and PocketTTS PCM/player path. The public fixture asks for six short sentences
explaining a voice assistant. No private prompt, tool operation or cloud call
is involved. [Raw live rows](benchmarks/v1.5.3-live-speech.json) and
[synthetic rows](benchmarks/v1.5.3-synthetic-speech.json) preserve the measurements.

## Live Qwen and real PocketTTS playback

The configured `qwen3.6-35b-a3b-uncensored-32k` model ran on its existing private
LAN llama.cpp server. PocketTTS was retained/preloaded on the Windows client;
real PCM was written to real ffplay. Hardware, model selection and server
defaults were unchanged. Each request explicitly used thinking off, a 512-token
cap, seed 42, temperature 1 and requested `cache_prompt=false`. All six requests
returned normal stop, 103 output tokens and the same 592-character semantic
answer/hash. Reasoning-token counts were not reported and remain null.

Three pairs alternated full-answer/incremental order. The first full-answer
request started with the LLM unloaded: first visible token arrived at 74.507 s,
first PCM pipe write at 78.089 s and playback completion at 117.987 s. This row
includes cold startup and is **excluded from warm averages**. The first pair
also includes PocketTTS's first actual generation after preload, so both rows
of that pair are excluded from the paired warm comparison below.

Warm means use two pairs (four requests), not a broad performance study:

| From request start, seconds | Full-answer waiting | Incremental speech |
| --- | ---: | ---: |
| First provider-visible text | 0.943 | 0.945 |
| First speakable chunk | 3.763 | 1.384 |
| First PCM received | 3.930 | 1.546 |
| First successful PCM pipe write | 3.930 | 1.546 |
| Client generation/stream completion | 3.763 | 32.296 |
| Speech completion | 43.950 | 37.729 |

First PCM pipe write improved by 2.384 s, about **60.7%**. Each incremental
trial wrote PCM before its observed client stream completed. The warm native
prompt times were about 0.88–0.96 s and native decode about 2.78–2.93 s across
both modes. Output streaming improves when useful speech starts; it does not
make model inference intrinsically faster.

The 32-second incremental client stream duration is deliberate **backpressure**:
the bounded queue blocks provider consumption while speech catches up. The
server can generate ahead into its/network buffers. Do not interpret client
stream completion as the server's exact final-token timestamp, or the longer
client duration as slower native inference. The pipe-write measurement proves
PCM enters playback, not the physical instant sound leaves the speaker; no
acoustic loopback was captured. Real playback was exercised, but speech quality
and barge-in were not evaluated or claimed. The tested model was explicitly
unloaded afterwards and its unloaded status verified.

## Synthetic regression benchmark

Three pairs use deterministic fixture text, 10 ms word arrivals, mock PCM and an
immediate player sink. All six rows completed with identical semantic hashes.
Mean first PCM write was 0.403 s waiting for the full answer versus 0.088 s
incrementally. Generation completed around 0.398 s in either mode; total mock
speech completion was 0.462 versus 0.477 s. These synthetic values demonstrate
ordering/overlap and must not be presented as real voice quality or hardware
latency. The benchmark and unit tests also cover empty/failed output without
claiming unavailable audio measurements.

## Limits and reproduction

Run the commands in [incremental speech](incremental-speech.md). The live test
uses a short public prompt rather than a full runtime intent catalogue/history,
so it excludes classification, real input queueing and microphone capture. It
does exercise the production output path. Runtime telemetry separately starts
at accepted request creation and includes input queue/intent time.

One model, one server/client setup, six total live requests and only two paired
warm repetitions cannot establish universal latency or accuracy parity. Speech
sampling can change completion length/duration even with identical text.
Different chunk boundaries, TTS providers, cold models, hidden reasoning and
device buffering can change results. Existing provider deadlines include time
blocked by speech; exceptionally long answers may require a suitable timeout
or the full-answer path. Acoustic full duplex remains separate work in #206.
