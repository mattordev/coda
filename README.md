# C.O.D.A Source Code


Source can be found in *main.py*. Just run it after installing *requirements.txt*.


## What does this program do?
C.O.D.A is a smart assistant that I've began to rewrite as the original code was very old and really didn't do what I wanted it to do. New features include LLM-backed casual conversation and responses, CMUSphinx offline voice processing and a new framework for making modules (subject to change). All of this is designed to be ran on a raspberry pi locally, with or without an internet connection.

C.O.D.A stands for **Cognitive Operational Data Assistant**, but this project was originally called H.A.D.E.S, aka Home And Data... something or other. This project started in 2016, but was forgotten and not worked on for many years. The initial concept was for this system/asisstant to act as a user interface and aid in general purpose tasks.

As you may of figured, there's not much thats "cognitive" about a basic smart asisstant. But later down the line, depening on the completion of the first prototype and what I manage to get done I also want to look at Machine Learning for more accurate wakeword detection and speech synthesis.

C.O.D.A has several planned commands and features, the planned commands and finished commands are here:

Planned:
  - Check system status (Temperature, storage, etc)
  - Create "project" folders & files
  - Spotify integration (so you can play your best tunes, whilst you do your best work)
  - Search the systems default browser for a query
  - Home automation (Change thermostat, lock doors, manage other home based sensors)
  
Finished:
  - Check for an internet connection

Other features (subject to change):
  - Flask integration with a webserver for showing what is happening system wide
  - System logging features for gathering information about the current platform
  
---
  
 ### Installation
 
Get the prerequisits first by doing the following:

Prereqs for pyaudio and speech recognition:
```sudo apt-get install libportaudio0 libportaudio2 libportaudiocpp0 portaudio19-dev flac```
 
then, finally, after cloning move to the download directory and run `pip install -r requirements.txt`

Speech-to-text notes:
- CODA now prefers local Whisper transcription through `faster-whisper` when available.
- Google speech recognition is still supported as a fallback.
- The first Whisper run will download the selected model.
- `faster-whisper` uses bundled FFmpeg libraries via PyAV, so you do not need a separate system `ffmpeg` install for the local Whisper path.

### Environment Variables

Create a `.env` file in the project root with:

```text
OPENAI_API_KEY=your-openai-key
ELEVENLABS_API_KEY=your-elevenlabs-key
CODA_STT_PROVIDER=auto
CODA_WHISPER_MODEL=auto
CODA_WHISPER_LANGUAGE=en
CODA_PAUSE_THRESHOLD=1.2
CODA_PHRASE_TIME_LIMIT=12
CODA_SYSTEM_PROMPT=
CODA_LLM_PROVIDER=openai
CODA_OPENAI_MODEL=gpt-4o-mini
CODA_LLM_FALLBACK=1
```

To use Ollama instead of OpenAI, switch the provider and point it at your host:

```text
ELEVENLABS_API_KEY=your-elevenlabs-key
CODA_STT_PROVIDER=auto
CODA_WHISPER_MODEL=auto
CODA_PAUSE_THRESHOLD=1.2
CODA_PHRASE_TIME_LIMIT=12
CODA_SYSTEM_PROMPT=
CODA_LLM_PROVIDER=ollama
CODA_OLLAMA_BASE_URL=http://192.168.0.64:30068
CODA_OLLAMA_MODEL=lfm2:24b
CODA_LLM_FALLBACK=1
```

Notes:
- `ELEVENLABS_API_KEY` is now the preferred source for TTS auth.
- Legacy `ELapi_key.txt` / `ELapikey.txt` files are still supported as fallback.
- `CODA_GPT_FALLBACK` is still accepted as a legacy alias for `CODA_LLM_FALLBACK`.
- If `CODA_OLLAMA_MODEL` is omitted, CODA prefers `lfm2:24b` when that model exists and otherwise falls back to the first model returned by Ollama's `/api/tags` endpoint.
- If no command word is detected after the wakeword, CODA falls back to the configured LLM so you can ask questions and continue the conversation naturally.
- `CODA_STT_PROVIDER=auto` prefers local Whisper via `faster-whisper`, then falls back to Google recognition.
- In `auto` mode, CODA will also step down to a more compatible local Whisper setup before using Google, for example when CUDA is detected but the local CUDA runtime is not actually usable.
- If a local Whisper attempt fails at runtime, CODA disables that exact attempt for the rest of the session so later utterances do not keep paying the same startup penalty. `debug reload` clears that session cache.
- LLM voice replies now open a short follow-up window so the next spoken reply can skip the wake word. Adjust this with `CODA_FOLLOWUP_TIMEOUT` in seconds.
- `CODA_STT_PROVIDER=google` keeps the legacy Google-only path.
- `CODA_WHISPER_MODEL=auto` picks a hardware-friendly default: `turbo` on CUDA systems, `small` on desktop CPU, and `base` on ARM boards such as Raspberry Pi.
- `CODA_WHISPER_LANGUAGE` is optional. Leave it unset for auto-detection, or set it to `en` for English-first command recognition.
- `CODA_PAUSE_THRESHOLD` controls how long a spoken pause CODA tolerates before it treats the utterance as finished. The default is now `1.2` seconds.
- `CODA_PHRASE_TIME_LIMIT` caps the maximum length of a single captured utterance. The default is now `12` seconds.
- Optional Whisper tuning: `CODA_WHISPER_DEVICE`, `CODA_WHISPER_COMPUTE_TYPE`, `CODA_WHISPER_BEAM_SIZE`, and `CODA_WHISPER_VAD_FILTER`.
- `CODA_SYSTEM_PROMPT` lets you override CODA's default assistant style without editing code. Run `debug reload` after changing it.
  
---
  
#### Licence

GNU AGPLv3

Copyright (C) 2022 Matthew Roberts

To view the full license, please view `LICENCE.md`
