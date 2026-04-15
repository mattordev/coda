import os
import re
from dataclasses import dataclass

import utils.llm_service as llm_service


@dataclass
class CommandResult:
    handled: bool
    response_text: str | None = None
    used_llm: bool = False
    open_follow_up: bool = False
    command_matches: tuple[str, ...] = ()

    def __bool__(self):
        return self.handled


def run(message, commands, debug=False):
    return on_command(message, commands, debug=debug)


def reload_config():
    llm_service.reload_config()


def _tokenize_message(message):
    return re.findall(r"[a-z0-9']+", message.lower())


def _ordered_matches(tokens, commands):
    matched = []
    for token in tokens:
        if token in commands and token not in matched:
            matched.append(token)
    return matched


def _llm_fallback_enabled():
    return llm_service.llm_fallback_enabled()


def _generate_llm_response(user_text):
    return llm_service.generate_llm_response(user_text)


def _describe_llm_fallback():
    return llm_service.describe_llm_fallback()


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
            return CommandResult(handled=False)

        if debug:
            print("[DEBUG] No command found. Routing message to LLM fallback.")

        if not _llm_fallback_enabled():
            return CommandResult(handled=False)

        provider = llm_service.get_llm_provider()
        if debug:
            print(f"[DEBUG] Using LLM fallback: {_describe_llm_fallback()}")
        response_text, error = _generate_llm_response(normalized_message)
        if not error and not response_text:
            if debug:
                print("[DEBUG] LLM returned an empty response. Retrying once.")
            response_text, error = _generate_llm_response(normalized_message)

        if error:
            if debug:
                print(f"[DEBUG] LLM fallback unavailable ({provider}): {error}")
            return CommandResult(handled=False)

        if not response_text:
            if debug:
                print("[DEBUG] LLM returned empty response twice. Keeping follow-up window open.")
            return CommandResult(
                handled=True,
                used_llm=True,
                open_follow_up=True,
            )

        print(f"CODA: {response_text}")
        try:
            import utils.speak_response as speak
            speak.speak_response(response_text)
        except Exception as exc:
            if debug:
                print(f"[DEBUG] Could not speak LLM response: {exc}")
        return CommandResult(
            handled=True,
            response_text=response_text,
            used_llm=True,
            open_follow_up=True,
        )

    command_executed = False
    executed_commands = []

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
            executed_commands.append(cmd)
        else:
            print("Command failed to execute... Please try again!")

    return CommandResult(
        handled=command_executed,
        command_matches=tuple(executed_commands),
    )


def clear_terminal():
    return os.system('cls')
