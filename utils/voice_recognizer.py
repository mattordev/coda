# TODO: Implement PocketSphinx for offline voice recognition and use google's API for when there is a connection

import speech_recognition as sr
# import pocketsphinx5 as ps5
import utils.speak_response as speak
import commands.connected as connected
import utils.on_command as command

# Wakeword is our list of trigger words, commands is the commands list and type defines whether the voicerecognition is in response to a question.
# Currently not using `wakewords.json` or `commands.json` but will be in the future


def run(wakeword, commands, type):
    while True:
        r = sr.Recognizer()
        with sr.Microphone() as source:
            # r.adjust_for_ambient_noise(source, duration=0.5)

            if (type == "normal"):
                print('Ready to accept commands', flush=True)
            elif (type == 'response'):
                print('waiting for response')
            try:
                audio = r.listen(source, timeout=.25)

                speech = (r.recognize_google(audio))
                # Needs editing
                # if (connected.run() == True):
                #     print ("LOG: Using online voice API")
                #     speech = (r.recognize_google(audio))
                # else:
                #     print ("LOG: Using offline voice API")
                #     speech = (r.recognize_sphinx(audio_data=audio))

                message = (speech.lower())
                display_message(message)

                if not set(wakeword).isdisjoint(message.lower().split()):
                    command.run(str(message), commands)

            # exceptions
            except sr.UnknownValueError:
                print("ERROR: Audio not understandable")
                continue
            except sr.RequestError as e:
                speak.speak_response(
                    "There was an issue with that request. Please try again later.")
            except sr.WaitTimeoutError:
                continue


def display_message(message):
    print(message, flush=True)
