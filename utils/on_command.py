import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    import openai
except ImportError:
    openai = None


if load_dotenv is not None:
    load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("CODA_OPENAI_MODEL", "gpt-3.5-turbo")

if openai is not None and OPENAI_API_KEY and hasattr(openai, "api_key"):
    openai.api_key = OPENAI_API_KEY

conversation = [
    {
        "role": "system",
        "content": (
            "Act as a personal assistant for the user. "
            "Your name is CODA, which stands for Cognitive Operational Data Assistant. "
            "Assist the user in daily tasks with concise and useful responses."
        ),
    },
]


def run(message, commands, debug=False):
    return on_command(message, commands, debug=debug)


def _ordered_matches(tokens, commands):
    matched = []
    for token in tokens:
        if token in commands and token not in matched:
            matched.append(token)
    return matched


def _gpt_fallback_enabled():
    return os.getenv("CODA_GPT_FALLBACK", "1").lower() in ("1", "true", "yes")


def _generate_gpt_response(user_text):
    if openai is None:
        return None, "openai package is not installed"

    if not OPENAI_API_KEY:
        return None, "OPENAI_API_KEY is not set"

    conversation.append({"role": "user", "content": user_text})

    try:
        # openai>=1.x client API
        if hasattr(openai, "OpenAI"):
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=conversation,
            )
            assistant_message = response.choices[0].message.content
        else:
            # openai<=0.x legacy API
            response = openai.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=conversation,
            )
            assistant_message = response["choices"][0]["message"]["content"]
    except Exception as exc:
        conversation.pop()
        return None, str(exc)

    assistant_message = (assistant_message or "").strip()
    if assistant_message:
        conversation.append({"role": "assistant", "content": assistant_message})

    return assistant_message, None


def on_command(msg, commands, debug=False):
    # Lowercase and split into command tokens.
    tokens = msg.lower().split()

    if debug:
        print(f"[DEBUG] Raw message: {msg}")
        print(f"[DEBUG] Tokens: {tokens}")
        print(f"[DEBUG] Available commands: {sorted(commands.keys())}")

    matched_commands = _ordered_matches(tokens, commands)

    if debug:
        print(f"[DEBUG] Matched commands: {matched_commands}")

    if not matched_commands:
        print("No command found in string")

        if not _gpt_fallback_enabled():
            return False

        response_text, error = _generate_gpt_response(msg.strip())
        if error:
            if debug:
                print(f"[DEBUG] GPT fallback unavailable: {error}")
            return False

        if not response_text:
            return False

        print(f"CODA: {response_text}")
        try:
            import utils.speak_response as speak
            speak.speak_response(response_text)
        except Exception as exc:
            if debug:
                print(f"[DEBUG] Could not speak GPT response: {exc}")
        return True

    command_executed = False

    for cmd in matched_commands:
        cmd_index = tokens.index(cmd)
        args = tokens[cmd_index:]

        if debug:
            print(f"[DEBUG] Running command '{cmd}' with args: {args}")

        try:
            result = commands[cmd].run(args)
        except Exception as exc:
            print(f"Command '{cmd}' raised an error: {exc}")
            continue

        if debug:
            print(f"[DEBUG] Command '{cmd}' returned: {result}")

        if result:
            command_executed = True
        else:
            print("Command failed to execute... Please try again!")

    return command_executed


def clear_terminal():
    return os.system('cls')
