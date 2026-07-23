import os
from dataclasses import dataclass

from ai.intents import IntentDispatcher, create_builtin_router
from ai.llm_router.core import route_request
import utils.llm_service as llm_service


_intent_router = create_builtin_router()


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
    global _intent_router

    llm_service.reload_config()
    _intent_router = create_builtin_router()


def _llm_fallback_enabled():
    return llm_service.llm_fallback_enabled()


def _dispatch_intent(
    message,
    commands,
    debug=False,
) -> CommandResult | None:
    """Route and dispatch an accepted intent or return None."""
    result = _intent_router.route(message)

    if debug:
        print(
            "[DEBUG - INTENT] "
            f"intent={result.intent.name if result.intent else None}, "
            f"confidence={result.confidence}, "
            f"strategy={result.strategy}, "
            f"accepted={result.accepted}, "
            f"error={result.error}"
        )

    if not result.accepted:
        return None

    request = result.to_request(message)
    dispatcher = IntentDispatcher(commands)
    intent_name = request.intent.name

    try:
        executed = dispatcher.dispatch(request)
    except Exception as exc:
        print(f"Command '{intent_name}' raised an error: {exc}")
        return CommandResult(handled=False)

    if not executed:
        print("Command failed to execute... Please try again!")

    return CommandResult(
        handled=executed,
        command_matches=(intent_name,) if executed else (),
    )


def on_command(msg, commands, debug=False):
    normalized_message = msg.strip()

    if debug:
        print(f"[DEBUG] Raw message: {msg}")
        print(
            f"[DEBUG] Available commands: "
            f"{sorted(commands.keys())}"
        )

    if not normalized_message:
        if debug:
            print(
                "[DEBUG] No command content remained "
                "after normalization."
            )

        return CommandResult(handled=False)

    dispatch_result = _dispatch_intent(
        normalized_message,
        commands,
        debug=debug,
    )

    if dispatch_result is not None:
        return dispatch_result

    if debug:
        print(
            "[DEBUG] No accepted intent. "
            "Routing message to LLM fallback."
        )

    if not _llm_fallback_enabled():
        return CommandResult(handled=False)

    response_text, error = route_request(normalized_message)

    if not error and not response_text:
        if debug:
            print(
                "[DEBUG] LLM returned an empty response. "
                "Retrying once."
            )

        response_text, error = route_request(normalized_message)

    if error:
        if debug:
            print(f"[DEBUG] LLM request failed: {error}")

        return CommandResult(handled=False)

    if not response_text:
        if debug:
            print(
                "[DEBUG] LLM returned empty response twice. "
                "Keeping follow-up window open."
            )

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


def clear_terminal():
    return os.system("cls")
