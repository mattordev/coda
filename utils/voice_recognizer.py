# TODO: Implement PocketSphinx for offline voice recognition and use google's API for when there is a connection

import speech_recognition as sr
import pocketsphinx5 as ps5
import utils.speak_response as speak
import commands.connected as connected


def run(wakeword):
    while True:
        r = sr.Recognizer()
        with sr.Microphone() as source:
            #r.adjust_for_ambient_noise(source, duration=0.5)
            print('Ready to accept commands')
            try:
                audio = r.listen(source, timeout=0.25)
                
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

                if not wakeword.isdisjoint(message.lower().split()):
                    main.on_command(str(message)) # Errors because code for this is still in main

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
