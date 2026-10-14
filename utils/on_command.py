import os
from copy import deepcopy
from dataclasses import dataclass
from threading import Event, Thread
import time

from ai.intents import (
    IntentDispatcher,
    create_command_registry,
    create_router,
)
from ai.llm_router.core import route_request
import utils.llm_service as llm_service
from ai.mcp.catalog import MCPRuntimeCatalog
from ai.tools.arguments import prepare_arguments
from ai.tools.context import prepare_tool_context
from ai.tools.models import ToolResult
from ai.privacy.sanitizer import sanitize_structure, sanitize_text


_intent_router = None
_intent_registry = None
_mcp_catalog = None
_pending_confirmation = None


@dataclass
class CommandResult:
    handled: bool
    response_text: str | None = None
    used_llm: bool = False
    open_follow_up: bool = False
    command_matches: tuple[str, ...] = ()
    tool_result: ToolResult | None = None

    def __bool__(self):
        return self.handled


def run(message, commands, debug=False, cancel_event: Event | None = None):
    return on_command(message, commands, debug=debug, cancel_event=cancel_event)


def configure_intent_router(commands, *, mcp_manager=None):
    """Configure intent routing from the loaded command modules."""
    global _intent_registry
    global _intent_router
    global _mcp_catalog
    global _pending_confirmation

    shutdown_tools()
    _pending_confirmation = None
    _mcp_catalog = MCPRuntimeCatalog(mcp_manager)

    _intent_registry = create_command_registry(commands)
    _intent_router = create_router(_intent_registry)


def start_tools():
    """Warm explicitly eager servers without blocking CODA startup."""
    if _mcp_catalog is not None:
        catalog = _mcp_catalog
        Thread(target=lambda: catalog.discover(eager_only=True), name="mcp-eager", daemon=True).start()


def shutdown_tools(*, timeout_seconds=10):
    global _pending_confirmation
    _pending_confirmation = None
    if _mcp_catalog is not None:
        return _mcp_catalog.close(timeout_seconds=timeout_seconds)
    return {}


def cancel_pending_confirmation():
    """A stop phrase cancels approval even when there is no active execution."""
    global _pending_confirmation
    had_pending = _pending_confirmation is not None
    _pending_confirmation = None
    return had_pending


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
    cancel_event=None,
) -> CommandResult | None:
    """Route and dispatch an accepted intent or return None."""
    global _intent_router
    global _intent_registry
    global _pending_confirmation

    if _intent_router is None:
        raise RuntimeError("Intent router has not been configured.")

    result = _intent_router.route(message)
    if not result.matched and _mcp_catalog is not None:
        intents = _mcp_catalog.discover(cancel_event=cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            return CommandResult(handled=False)
        if intents:
            registry = create_command_registry(commands)
            for intent in intents:
                registry.register(intent)
            _intent_registry = registry
            _intent_router = create_router(registry)
            result = _intent_router.route(message)

    if debug:
        print(
            "[DEBUG - INTENT] "
            f"intent={result.intent.name if result.intent else None}, "
            f"confidence={result.confidence}, "
            f"strategy={result.strategy}, "
            f"accepted={result.accepted}, "
            f"routing_error={bool(result.error)}"
        )

    if not result.accepted:
        if result.needs_clarification:
            return CommandResult(handled=True, response_text="Please clarify which action you want me to perform.", open_follow_up=True)
        if message.lower().startswith("mcp."):
            return CommandResult(handled=True, response_text="That MCP tool is unavailable. Check its configuration and discovery status.")
        return None

    if result.intent.input_schema is not None:
        try:
            arguments = prepare_arguments(result, message, cancel_event=cancel_event)
        except Exception:
            return CommandResult(handled=True, response_text="The tool arguments are invalid or incomplete. Please provide the required values.", open_follow_up=True)
        request = result.to_request(message, arguments=arguments)
        if _mcp_catalog is None:
            return CommandResult(handled=True, response_text="The tool is unavailable.")
        try:
            executor = _mcp_catalog.manager.executor(request.intent.name, cancel_event=cancel_event)
        except ValueError:
            return CommandResult(handled=True, response_text="The tool is unavailable.")
        dispatcher = IntentDispatcher(commands)
        dispatcher.register(request.intent.name, executor)
        outcome = dispatcher.execute(request)
        if outcome.error is not None and outcome.error.code == "confirmation_required":
            _pending_confirmation = (deepcopy(request), executor, time.monotonic() + 60)
            return CommandResult(
                handled=True, response_text=f"Confirm the action {request.intent.name}? Say confirm or cancel.",
                open_follow_up=True, tool_result=outcome,
            )
        return _tool_outcome(request, outcome, cancel_event=cancel_event)

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


def _tool_outcome(request, outcome, *, cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        return CommandResult(handled=False)
    llm_service.record_tool_exchange(request.message, outcome)
    if outcome.success:
        display = ToolResult(success=True, data=sanitize_structure(outcome.data))
        representation = prepare_tool_context(display, for_cloud=False, max_chars=1200)
        response = f"Tool completed. {representation}" if representation else "Tool completed."
    else:
        response = outcome.error.message if outcome.error else "The tool failed."
    print(f"CODA: {response}")
    return CommandResult(
        handled=True, response_text=response, open_follow_up=True,
        command_matches=(request.intent.name,), tool_result=outcome,
    )


def on_command(msg, commands, debug=False, cancel_event: Event | None = None):
    global _pending_confirmation
    normalized_message = msg.strip()

    cancelled_result = _get_cancellation_result(cancel_event, debug)
    if cancelled_result is not None:
        _pending_confirmation = None
        return cancelled_result

    if debug:
        print(f"[DEBUG] Message: {sanitize_text(msg)}")
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

    pending = _pending_confirmation
    _pending_confirmation = None
    if pending is not None:
        request, executor, expires = pending
        answer = normalized_message.lower().rstrip(".!?")
        if answer in ("cancel", "no", "deny"):
            return CommandResult(handled=True, response_text="Action cancelled.")
        if answer in ("confirm", "yes", "approve"):
            if time.monotonic() >= expires:
                return CommandResult(handled=True, response_text="That confirmation expired. Please request the action again.")
            return _tool_outcome(request, executor.execute(request, cancel_event=cancel_event, confirmed=True), cancel_event=cancel_event)

    dispatch_result = _dispatch_intent(
        normalized_message,
        commands,
        debug=debug,
        cancel_event=cancel_event,
    )

    cancelled_result = _get_cancellation_result(cancel_event, debug)
    if cancelled_result is not None:
        _pending_confirmation = None
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
