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

### Environment Variables

Create a `.env` file in the project root with:

```text
OPENAI_API_KEY=your-openai-key
ELEVENLABS_API_KEY=your-elevenlabs-key
CODA_LLM_PROVIDER=openai
CODA_OPENAI_MODEL=gpt-4o-mini
CODA_LLM_FALLBACK=1
```

To use Ollama instead of OpenAI, switch the provider and point it at your host:

```text
ELEVENLABS_API_KEY=your-elevenlabs-key
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
  
---
  
#### Licence

GNU AGPLv3

Copyright (C) 2022 Matthew Roberts

To view the full license, please view `LICENCE.md`
