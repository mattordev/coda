from gtts import gTTS
import random
import os

def speak(rand,n):
    tts = gTTS(text=random.choice(rand), lang='en')                 
    tts.save('CODA'+str(n)+'.mp3')
