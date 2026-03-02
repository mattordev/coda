import webbrowser
import random

# TODO: GET wakewords from JSON and append them at the end of the stopwords list.


def extract_query_from_command(command):
    excluded_words = ['google', 'maps', 'search', 'find', 'look', 'up', 'show', 'display', 'navigate',
                      'to', 'directions', 'route', 'get', 'take', 'me', 'on', 'in', 'near', 'around', 'nearby', 'for']
    querywords = [word for word in command if word.lower()
                  not in excluded_words]
    query = ' '.join(querywords)
    return query.strip()


def run(message):
    query = extract_query_from_command(message)
    if query:
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        webbrowser.register(
            'chrome', None, webbrowser.BackgroundBrowser(chrome_path))
        webbrowser.get('chrome').open(
            "https://www.google.be/maps/place/" + query, new=2)
        print(random.choice([query + ' on Google Maps', 'Maps loading...']))
        return True  # Command executed successfully
    else:
        print("No valid query found")
        return False  # Command failed to execute
