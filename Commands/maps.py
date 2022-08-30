import webbrowser
import random

# need to rewrite this so it asks for where to search on google maps
def run(message):
    query = message
    stopwords = ['google', 'maps']
    querywords = query.split()
    resultwords  = [word for word in querywords if word.lower() not in stopwords]
    result = ' '.join(resultwords)
    Chrome = ("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe %s")
    webbrowser.get(Chrome).open("https://www.google.be/maps/place/"+result+"/")
    print(random.choice([result+'on google maps', 'Maps loading...']))