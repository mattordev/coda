import os


def run(message, commands, debug=False):
    return on_command(message, commands, debug=debug)


def _ordered_matches(tokens, commands):
    matched = []
    for token in tokens:
        if token in commands and token not in matched:
            matched.append(token)
    return matched


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
        return False

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
