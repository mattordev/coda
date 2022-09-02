# C.O.D.A Source Code


Source can be found in *main.py*. Just run it after installing *requirements.txt*.


## What does this program do?
C.O.D.A is a smart assistant that I've began to rewrite as the original code was very old and really didn't do what I wanted it to do. New features include gpt-3 integration for casual converstation and responses, CMUSphinx offline voice processing and a new framework for making modules (subject to change). All of this is designed to be ran on a raspberry pi locally, with or without an internet connection.

C.O.D.A stands for **Cognitive Operational Data Assistant**, but this project was originally called H.A.D.E.S, aka Home And Data... something or other. This project started in 2016, but was forgotten and not worked on for many years. The initial concept was for this system/asisstant to act as a user interface and aid in general purpose tasks.

As you may of figured, there's not much thats "cognitive" about a basic smart asisstant. But later down the line, depening on the completion of the first prototype and what I manage to get done I also want to look at Machine Learning for more accurate wakeword detection and speech synthesis.

C.O.D.A has several planned commands and features, the planned commands and finished commands are here:

Planned:
  - Check system status (Temperature, storage, etc)
  - Create "project" folders & files
  - Spotify integration (so you can play your best tunes, whilst you do your best work)
  - Search the systems default browser for a query.
  - Home automation (Change thermostat, lock doors, manage other home based sensors)
  
Finished:
  - Check for an internet connection
  
---
  
 ### Installation
 
Get the prerequisits first by doing the following:

Prereqs for pyaudio and speech recognition:
```sudo apt-get install libportaudio0 libportaudio2 libportaudiocpp0 portaudio19-dev flac```
 
then, finally, after cloning move to the download directory and run `pip install -r requirements.txt`
  
---
  
#### Licence

GNU AGPLv3

Copyright (c) 2022 Matthew Roberts

To view the full license, please view `LICENCE.md`
