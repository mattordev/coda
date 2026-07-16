import os

try:
    import openai
except ImportError:
    openai = None

def get_api_key():
    return os.getenv ("OPENAI_API_KEY", "").strip()
    
def get_model():
    return os.getenv("CODA_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

def reload_config():
    api_key = get_api_key()
    
    if openai is not None and hasattr(openai, "api_key"):
        openai.api_key = api_key or None
        
def describe():
    return f"openai (model: {get_model()})"

def generate(messages):
    api_key = get_api_key()
    model = get_model()
    
    if openai is None:
        return None, "openai package is not installed."
    
    if not api_key:
        return None, "OPENAI_API_KEY is not set in env."
    
    if hasattr(openai, "api_key"):
        openai.api_key = api_key
        
    try:
        if hasattr (openai, "OpenAI"):
            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            assistant_message = response.choices[0].message.content
        else:
            chat_completion = getattr(openai, "ChatCompletion", None)
            if chat_completion is None:
                return None, "OpenAI package does not expose a legacy ChatCompletion API"

            response = chat_completion.create(
                model=model,
                messages=messages,
            )
            assistant_message = response["choices"][0]["message"]["content"]
    except Exception as exc:
        return None, str(exc)
    
    return (assistant_message or "").strip(), None