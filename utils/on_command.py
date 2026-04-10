import os
import re

import requests

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
ollama_model_cache = None
preferred_ollama_models = (
    "lfm2:24b",
)


def run(message, commands, debug=False):
    return on_command(message, commands, debug=debug)


def _get_float_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def _get_openai_api_key():
    return os.getenv("OPENAI_API_KEY", "").strip()


def _get_openai_model():
    return os.getenv("CODA_OPENAI_MODEL", "gpt-3.5-turbo").strip() or "gpt-3.5-turbo"


def _get_llm_timeout_seconds():
    return _get_float_env("CODA_LLM_TIMEOUT", 45.0)


def reload_config():
    global ollama_model_cache

    if load_dotenv is not None:
        load_dotenv(override=True)

    ollama_model_cache = None

    openai_api_key = _get_openai_api_key()
    if openai is not None and hasattr(openai, "api_key"):
        openai.api_key = openai_api_key or None


def _tokenize_message(message):
    return re.findall(r"[a-z0-9']+", message.lower())


def _ordered_matches(tokens, commands):
    matched = []
    for token in tokens:
        if token in commands and token not in matched:
            matched.append(token)
    return matched


def _llm_fallback_enabled():
    configured_value = os.getenv("CODA_LLM_FALLBACK")
    if configured_value is None:
        configured_value = os.getenv("CODA_GPT_FALLBACK", "1")
    return configured_value.lower() in ("1", "true", "yes")


def _get_ollama_base_url():
    return os.getenv("CODA_OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _get_configured_ollama_model():
    return os.getenv("CODA_OLLAMA_MODEL", "").strip()


def _get_llm_provider():
    provider = os.getenv("CODA_LLM_PROVIDER", "").strip().lower()

    if provider == "gpt":
        return "openai"

    if provider in ("", "auto"):
        if _get_configured_ollama_model() or os.getenv("CODA_OLLAMA_BASE_URL"):
            return "ollama"
        return "openai"

    return provider


def _get_ollama_model():
    global ollama_model_cache

    configured_model = _get_configured_ollama_model()
    if configured_model:
        return configured_model, None

    if ollama_model_cache:
        return ollama_model_cache, None

    ollama_base_url = _get_ollama_base_url()

    try:
        response = requests.get(
            f"{ollama_base_url}/api/tags",
            timeout=min(_get_llm_timeout_seconds(), 10),
        )
        response.raise_for_status()
        models = response.json().get("models", [])
    except Exception as exc:
        return None, (
            "could not fetch Ollama models automatically. "
            "Set CODA_OLLAMA_MODEL explicitly. "
            f"Details: {exc}"
        )

    if not models:
        return None, (
            "no Ollama models were found at the configured host. "
            "Set CODA_OLLAMA_MODEL after pulling a model."
        )

    model_names = []
    for model in models:
        model_name = model.get("name") or model.get("model")
        if model_name:
            model_names.append(model_name)

    selected_model = None

    for preferred_model in preferred_ollama_models:
        if preferred_model in model_names:
            selected_model = preferred_model
            break

    if selected_model is None:
        selected_model = model_names[0] if model_names else None

    if not selected_model:
        return None, "Ollama returned models but none included a usable name"

    ollama_model_cache = selected_model
    return ollama_model_cache, None


def _generate_openai_response(user_text):
    openai_api_key = _get_openai_api_key()
    openai_model = _get_openai_model()

    if openai is None:
        return None, "openai package is not installed"

    if not openai_api_key:
        return None, "OPENAI_API_KEY is not set"

    if hasattr(openai, "api_key"):
        openai.api_key = openai_api_key

    conversation.append({"role": "user", "content": user_text})

    try:
        # openai>=1.x client API
        if hasattr(openai, "OpenAI"):
            client = openai.OpenAI(api_key=openai_api_key)
            response = client.chat.completions.create(
                model=openai_model,
                messages=conversation,
            )
            assistant_message = response.choices[0].message.content
        else:
            # openai<=0.x legacy API
            response = openai.ChatCompletion.create(
                model=openai_model,
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


def _generate_ollama_response(user_text):
    model_name, error = _get_ollama_model()
    if error:
        return None, error

    ollama_base_url = _get_ollama_base_url()
    conversation.append({"role": "user", "content": user_text})

    try:
        response = requests.post(
            f"{ollama_base_url}/api/chat",
            json={
                "model": model_name,
                "messages": conversation,
                "stream": False,
            },
            timeout=_get_llm_timeout_seconds(),
        )
        response.raise_for_status()
        response_json = response.json()
        message = response_json.get("message", {})
        assistant_message = message.get("content") or response_json.get("response")
    except Exception as exc:
        conversation.pop()
        return None, str(exc)

    assistant_message = (assistant_message or "").strip()
    if assistant_message:
        conversation.append({"role": "assistant", "content": assistant_message})

    return assistant_message, None


def _generate_llm_response(user_text):
    provider = _get_llm_provider()

    if provider == "openai":
        return _generate_openai_response(user_text)

    if provider == "ollama":
        return _generate_ollama_response(user_text)

    return None, (
        f"unsupported CODA_LLM_PROVIDER '{provider}'. "
        "Expected 'openai' or 'ollama'."
    )


def _describe_llm_fallback():
    provider = _get_llm_provider()

    if provider == "openai":
        return f"openai (model: {_get_openai_model()})"

    if provider == "ollama":
        model_name, error = _get_ollama_model()
        if error:
            return f"ollama (model resolution failed: {error})"
        return f"ollama (model: {model_name})"

    return provider


def on_command(msg, commands, debug=False):
    normalized_message = msg.strip()
    tokens = _tokenize_message(normalized_message)

    if debug:
        print(f"[DEBUG] Raw message: {msg}")
        print(f"[DEBUG] Tokens: {tokens}")
        print(f"[DEBUG] Available commands: {sorted(commands.keys())}")

    matched_commands = _ordered_matches(tokens, commands)

    if debug:
        print(f"[DEBUG] Matched commands: {matched_commands}")

    if not matched_commands:
        if not normalized_message:
            if debug:
                print("[DEBUG] No command content remained after normalization.")
            return False

        if debug:
            print("[DEBUG] No command found. Routing message to LLM fallback.")

        if not _llm_fallback_enabled():
            return False

        provider = _get_llm_provider()
        if debug:
            print(f"[DEBUG] Using LLM fallback: {_describe_llm_fallback()}")
        response_text, error = _generate_llm_response(normalized_message)
        if error:
            if debug:
                print(f"[DEBUG] LLM fallback unavailable ({provider}): {error}")
            return False

        if not response_text:
            return False

        print(f"CODA: {response_text}")
        try:
            import utils.speak_response as speak
            speak.speak_response(response_text)
        except Exception as exc:
            if debug:
                print(f"[DEBUG] Could not speak LLM response: {exc}")
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
