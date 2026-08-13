import os
from dataclasses import dataclass
from threading import Event

from ai.intents import (
    IntentDispatcher,
    create_command_registry,
    create_router,
)
from ai.llm_router.core import route_request
import utils.llm_service as llm_service


_intent_router = None
_intent_registry = None


@dataclass
class CommandResult:
    handled: bool
    response_text: str | None = None
    used_llm: bool = False
    open_follow_up: bool = False
    command_matches: tuple[str, ...] = ()

    def __bool__(self):
        return self.handled


def run(message, commands, debug=False, cancel_event: Event | None = None):
    return on_command(message, commands, debug=debug, cancel_event=cancel_event)


def configure_intent_router(commands):
    """Configure intent routing from the loaded command modules."""
    global _intent_registry
    global _intent_router

    _intent_registry = create_command_registry(commands)
    _intent_router = create_router(_intent_registry)


def reload_config():
    global _intent_router

    llm_service.reload_config()
    if _intent_registry is None:
        raise RuntimeError("Intent router has not been configured.")

    _intent_router = create_router(_intent_registry)


def _llm_fallback_enabled():
    return llm_service.llm_fallback_enabled()


def _get_cancellation_result(
    cancel_event: Event | None,
    debug: bool,
) -> CommandResult | None:
    if cancel_event is None or not cancel_event.is_set():
        return None

    if debug:
        print("[DEBUG] Command processing cancelled.")

    return CommandResult(handled=False)


def _dispatch_intent(
    message,
    commands,
    debug=False,
) -> CommandResult | None:
    """Route and dispatch an accepted intent or return None."""
    if _intent_router is None:
        raise RuntimeError("Intent router has not been configured.")

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


def on_command(msg, commands, debug=False, cancel_event: Event | None = None):
    normalized_message = msg.strip()

    cancelled_result = _get_cancellation_result(cancel_event, debug)
    if cancelled_result is not None:
        return cancelled_result

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

    cancelled_result = _get_cancellation_result(cancel_event, debug)
    if cancelled_result is not None:
        return cancelled_result

    if dispatch_result is not None:
        return dispatch_result

    if debug:
        print(
            "[DEBUG] No accepted intent. "
            "Routing message to LLM fallback."
        )

    if not _llm_fallback_enabled():
        return CommandResult(handled=False)

    response_text, error = route_request(
        normalized_message,
        cancel_event=cancel_event,
    )

    cancelled_result = _get_cancellation_result(cancel_event, debug)
    if cancelled_result is not None:
        return cancelled_result

    if not error and not response_text:
        if debug:
            print(
                "[DEBUG] LLM returned an empty response. "
                "Retrying once."
            )

        response_text, error = route_request(
            normalized_message,
            cancel_event=cancel_event,
        )

        cancelled_result = _get_cancellation_result(cancel_event, debug)
        if cancelled_result is not None:
            return cancelled_result

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

    return CommandResult(
        handled=True,
        response_text=response_text,
        used_llm=True,
        open_follow_up=True,
    )


def clear_terminal():
    return os.system("cls")
