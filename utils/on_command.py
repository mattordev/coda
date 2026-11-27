import os
import json
import re
from utils.console import print_line as print
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
from ai.request_context import request_context
import utils.llm_service as llm_service
from ai.mcp.catalog import MCPRuntimeCatalog
from ai.mcp.conversation import plan_follow_up, focus_is_current, MISSING
from ai.mcp.web_sources import plan_sources, run_sources, source_seed, MISSING_SOURCE
from ai.tools.arguments import prepare_arguments
from ai.tools.presentation import prepare_tool_display, prepare_full_tool_output
from ai.tools.diagnostics import record_tool_failure
from ai.tools.models import ToolResult
from ai.tools.interpretation import interpret_result, is_result_follow_up
from ai.intents.router import IntentRouter, ExactMatchStrategy, ExampleMatchStrategy, CommandPrefixStrategy
from ai.privacy.sanitizer import sanitize_text, sanitize_structure
from ai.tools.error_feedback import safe_diagnostic
from ai.tools.result_summary import _presentation_style, _question_text
from utils.web_requests import normalize_spoken, browser_request
from ai.tools.captures import retain_capture
from runtime.schedule_commands import schedule_command
from runtime.action_commands import action_command


_intent_router = None
_intent_registry = None
_mcp_catalog = None
_pending_confirmation = None
_pending_source_read = None


def _cancel_source_offer():
    global _pending_source_read
    _pending_source_read = None


def _clear_non_web_focus():
    """Stopping work preserves completed web observations, not pending actions."""
    previous = llm_service.get_tool_follow_up_context()
    context = previous.host_context if previous is not None else None
    if not isinstance(context, dict) or not (context.get('bundle') == 'websearch' or context.get('source_seed')):
        llm_service.clear_tool_focus()


@dataclass
class CommandResult:
    handled: bool
    response_text: str | None = None
    used_llm: bool = False
    open_follow_up: bool = False
    command_matches: tuple[str, ...] = ()
    tool_result: ToolResult | None = None
    display_text: str | None = None
    error: str | None = None

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
    llm_service.clear_tool_focus()
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
    _cancel_source_offer()
    llm_service.clear_tool_focus()
    if _mcp_catalog is not None:
        return _mcp_catalog.close(timeout_seconds=timeout_seconds)
    return {}


def cancel_pending_confirmation():
    """A stop phrase cancels approval even when there is no active execution."""
    global _pending_confirmation
    had_pending = _pending_confirmation is not None
    _pending_confirmation = None
    _cancel_source_offer()
    _clear_non_web_focus()
    return had_pending


def reload_config():
    global _intent_router

    _cancel_source_offer()
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
    user_message=None,
) -> CommandResult | None:
    """Route and dispatch an accepted intent or return None."""
    global _intent_router
    global _intent_registry
    global _pending_confirmation

    if _intent_router is None:
        raise RuntimeError("Intent router has not been configured.")

    explicit_tool = message.split(maxsplit=1)[0].lower()
    is_explicit_mcp = explicit_tool.startswith("mcp.")
    if is_explicit_mcp:
        parts = explicit_tool.split(".", maxsplit=2)
        if len(parts) != 3 or not parts[1] or not parts[2] or _mcp_catalog is None:
            return CommandResult(handled=True, response_text="That MCP tool is unavailable. Check its configuration and discovery status.")
        intents = _mcp_catalog.discover(server_id=parts[1], cancel_event=cancel_event)
        if debug:
            print("[MCP DEBUG] Errors:", _mcp_catalog.errors)
            print("[MCP DEBUG] Discovered intents:", [intent.name for intent in intents])
        if cancel_event is not None and cancel_event.is_set():
            return CommandResult(handled=False)
        registry = create_command_registry(commands)
        for intent in intents:
            registry.register(intent)
        _intent_registry = registry
        _intent_router = create_router(registry)
        # An explicit canonical name needs no model interpretation, including
        # when the named server/tool is unavailable or misspelled.
        result = IntentRouter(registry, strategies=(ExactMatchStrategy(), CommandPrefixStrategy())).route(message)
    elif _mcp_catalog is not None and _intent_registry is not None:
        result = IntentRouter(_intent_registry, strategies=(
            ExactMatchStrategy(), ExampleMatchStrategy(), CommandPrefixStrategy(),
        )).route(message)
    else:
        result = _intent_router.route(message)
    if not is_explicit_mcp and not result.matched and _mcp_catalog is not None:
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
        if result.error is not None:
            print(f"[DEBUG - INTENT ERROR] {safe_diagnostic(repr(result.error))}")

    if not result.accepted:
        if result.error:
            return CommandResult(
                handled=True,
                response_text="I couldn't determine which action to run because intent routing failed. No action was run. Please try again or use an explicit command.",
            )
        if result.needs_clarification:
            return CommandResult(handled=True, response_text="Please clarify which action you want me to perform.", open_follow_up=True)
        if message.lower().startswith("mcp."):
            return CommandResult(handled=True, response_text="That MCP tool is unavailable. Check its configuration and discovery status.")
        return None

    if result.intent.input_schema is not None:
        try:
            arguments = prepare_arguments(result, message, cancel_event=cancel_event)
            if debug:
                print(f"[DEBUG - MCP ARGUMENTS] {sanitize_structure(arguments)!r}")
        except Exception:
            return CommandResult(handled=True, response_text="The tool arguments are invalid or incomplete. Please provide the required values.", open_follow_up=True)
        request = result.to_request(user_message or message, arguments=arguments)
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
            confirmation = f"Confirm the action {request.intent.name}? Say confirm or cancel."
            if isinstance(outcome.data,dict) and isinstance(outcome.data.get('browser_action'),dict):
                action = outcome.data['browser_action']
                confirmation = ('Submit the inspected form to ' + sanitize_text(action['destination']) +
                                ' with these exact fields: ' + sanitize_text(action['body']) +
                                '? Say confirm or cancel.')
            parts = request.intent.name.split('.', 2)
            if (len(parts) == 3 and parts[2] in ('create_file', 'create_directory')
                    and _mcp_catalog.manager.filesystem_focus_token(parts[1]) is not None):
                kind = 'file' if parts[2] == 'create_file' else 'folder'
                confirmation = f'Create the {kind} at "{sanitize_text(str(arguments.get("path", "")))}"? Say confirm or cancel.'
            if (len(parts) == 3 and parts[2] == 'delete_path'
                    and _mcp_catalog.manager.filesystem_focus_token(parts[1]) is not None):
                target = sanitize_text(str(arguments.get('path', '')))
                scope = ' including all its contents' if arguments.get('recursive') is True else ' (files and empty folders only)'
                confirmation = f'Permanently delete "{target}"{scope}? Say confirm or cancel.'
            if (len(parts) == 3 and parts[2] in ('copy_path', 'move_path')
                    and _mcp_catalog.manager.filesystem_focus_token(parts[1]) is not None):
                source = sanitize_text(str(arguments.get('source', '')))
                destination = sanitize_text(str(arguments.get('destination', '')))
                if parts[2] == 'move_path':
                    confirmation = f'Move "{source}" and any folder contents to the new location "{destination}"? Say confirm or cancel.'
                else:
                    confirmation = f'Copy "{source}" to the new location "{destination}"? Say confirm or cancel.'
            if (len(parts) == 3 and parts[2] in ('edit_file', 'append_file')
                    and _mcp_catalog.manager.filesystem_focus_token(parts[1]) is not None):
                target = sanitize_text(str(arguments.get('path', '')))
                content = str(arguments.get('new_text' if parts[2] == 'edit_file' else 'content', ''))
                preview = sanitize_text(content[:120]).replace('\r', '\\r').replace('\n', '\\n')
                suffix = ' (excerpt)' if len(content) > 120 else ''
                if parts[2] == 'edit_file':
                    old = sanitize_text(str(arguments.get('old_text', ''))[:120]).replace('\r', '\\r').replace('\n', '\\n')
                    confirmation = f'Replace one occurrence of "{old}" with "{preview}"{suffix} in "{target}"? Say confirm or cancel.'
                else:
                    confirmation = f'Append "{preview}"{suffix} to "{target}"? Say confirm or cancel.'
            return CommandResult(
                handled=True, response_text=confirmation,
                open_follow_up=True, tool_result=outcome,
            )
        return _tool_outcome(request, outcome, cancel_event=cancel_event, debug=debug)

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


def _tool_outcome(request, outcome, *, cancel_event=None, debug=False):
    global _pending_source_read
    if cancel_event is not None and cancel_event.is_set():
        return CommandResult(handled=False)
    outcome = retain_capture(outcome)
    interpreted = interpret_result(outcome, request.message, cancel_event=cancel_event)
    if interpreted.text is None or (cancel_event is not None and cancel_event.is_set()):
        return CommandResult(handled=False)
    response = interpreted.text
    offer = None
    payload = outcome.data.get('structuredContent', outcome.data) if isinstance(outcome.data, dict) else None
    if (outcome.success and _mcp_catalog is not None and isinstance(payload, dict)
            and (payload.get('kind') == 'web_search' or payload.get('scope') == 'search_snippets_only')):
        candidate = plan_sources('read the first source', outcome, _mcp_catalog.manager)
        if candidate is not None and candidate.kind == 'read':
            offer = candidate
            response += ' Would you like me to read the first source?'
    if (outcome.success and outcome.host_context and outcome.host_context.get('tool') == 'list_directory'
            and _presentation_style(_question_text(request.message)) == 'overview'
            and not any(word in request.message.casefold() for word in ('how many', 'count', 'number of'))):
        response += ' Would you like the filenames or the most recently modified files?'
    display = prepare_tool_display(outcome)
    if not llm_service.record_tool_exchange(request.message, outcome, response, cancel_event=cancel_event):
        return CommandResult(handled=False)
    if offer is not None and not (cancel_event is not None and cancel_event.is_set()):
        _pending_source_read = offer
    if not outcome.success:
        record_tool_failure(request.intent.name, display)
    if debug:
        print(f"[MCP TOOL] {request.intent.name}: {'completed' if outcome.success else 'failed'}")
        print(f"[MCP OUTPUT]\n{display}")
    return CommandResult(
        handled=True, response_text=response, used_llm=interpreted.used_llm, open_follow_up=True,
        command_matches=(request.intent.name,), tool_result=outcome,
        display_text=display if outcome.success else response,
    )


def _retained_tool_reply(message, *, cancel_event=None, debug=False):
    """Inspect retained output or answer a narrow follow-up without executing tools."""
    if message.casefold().rstrip('.!?') in ('show tool output', 'show last tool output', 'show full tool output'):
        result = llm_service.get_recent_tool_result()
        response = "There is no retained tool result in this conversation."
        display = None
        if result is not None:
            full = message.casefold().startswith('show full')
            display = prepare_full_tool_output(result) if full else prepare_tool_display(result)
            response = "Here is the retained tool output."
            if display.endswith('\N{HORIZONTAL ELLIPSIS}'):
                response = ("The full output exceeds the console safety limit; the complete result remains retained in memory."
                            if full else "These details are an excerpt. Say show full tool output for the retained output.")
            if cancel_event is not None and cancel_event.is_set():
                return CommandResult(handled=False)
            print(f"[MCP OUTPUT]\n{display}")
        return CommandResult(True, response_text=response, display_text=display, tool_result=result)
    if not is_result_follow_up(message):
        return None
    result = llm_service.get_tool_follow_up_context()
    if result is None:
        response = 'Which file or tool result do you mean? Please provide an absolute path or retrieve the result first.'
        return CommandResult(True, response_text=response, open_follow_up=True)
    interpreted = interpret_result(result, message, cancel_event=cancel_event, force=True)
    if interpreted.text is None or (cancel_event is not None and cancel_event.is_set()):
        return CommandResult(handled=False)
    if not llm_service.record_tool_follow_up(message, result, interpreted.text, cancel_event=cancel_event):
        return CommandResult(handled=False)
    if debug:
        print("[MCP CONTEXT] Answering from the retained tool result; no tool call.")
    return CommandResult(True, response_text=interpreted.text, used_llm=interpreted.used_llm,
                         open_follow_up=True, tool_result=result,
                         display_text=prepare_tool_display(result) if result.success else interpreted.text)


def _filesystem_follow_up(message, commands, *, cancel_event=None, debug=False):
    if _mcp_catalog is None:
        return None
    previous = llm_service.get_tool_follow_up_context()
    web_context = previous is not None and previous.host_context and (
        previous.host_context.get('bundle') in ('websearch', 'webread') or 'source_seed' in previous.host_context)
    if web_context:
        return None
    if previous is not None and previous.host_context and not focus_is_current(previous, _mcp_catalog.manager):
        llm_service.clear_tool_focus()
        previous = None
    plan = plan_follow_up(message, previous, _mcp_catalog.manager)
    if plan is None:
        return None
    if plan.tool:
        server = previous.host_context['server']
        command = f'mcp.{server}.{plan.tool} ' + json.dumps(plan.arguments)
        return _dispatch_intent(command, commands, cancel_event=cancel_event, debug=debug, user_message=message)
    if plan.result is not None:
        from ai.tools.presentation import prepare_tool_response
        question = message if _presentation_style(message.casefold()) == 'recent_files' else 'list all filenames'
        response = plan.reply + prepare_tool_response(plan.result, question=question)
        if not llm_service.record_tool_exchange(message, plan.result, response, cancel_event=cancel_event):
            return CommandResult(False)
        return CommandResult(True, response_text=response, open_follow_up=True, tool_result=plan.result,
                             display_text=prepare_tool_display(plan.result))
    if plan.reply == MISSING:
        llm_service.clear_tool_focus()
    return CommandResult(True, response_text=plan.reply, open_follow_up=True)


def _web_sources_reply(message, *, cancel_event=None, debug=False):
    if _mcp_catalog is None:
        return None
    plan = plan_sources(message, llm_service.get_tool_follow_up_context(), _mcp_catalog.manager)
    if plan is None:
        return None
    return _execute_source_plan(plan, message, cancel_event=cancel_event, debug=debug)


def _execute_source_plan(plan, message, *, cancel_event=None, debug=False):
    outcome, reply = run_sources(plan, _mcp_catalog, message, cancel_event=cancel_event)
    if reply is not None:
        return CommandResult(True, response_text=reply, open_follow_up=True)
    from ai.intents.models import Intent, IntentRequest
    request = IntentRequest(Intent('web.sources', 'Read selected source observations.', source='workflow'),
                            message, 1.0, 'web_sources')
    return _tool_outcome(request, outcome, cancel_event=cancel_event, debug=debug)


def on_command(msg, commands, debug=False, cancel_event: Event | None = None):
    """Emit the single finalized response used by every input and speech path."""
    with request_context(cancel_event=cancel_event) as context:
        result = _on_command(msg, commands, debug=debug, cancel_event=cancel_event)
    if cancel_event is not None and cancel_event.is_set():
        _cancel_source_offer()
        _clear_non_web_focus()
        return CommandResult(handled=False)
    if result.response_text and not (context.display_stream is not None and context.display_stream.started):
        print(f"CODA: {result.response_text}")
    return result


def _on_command(msg, commands, debug=False, cancel_event: Event | None = None):
    global _pending_confirmation
    global _pending_source_read
    normalized_message = normalize_spoken(msg)

    cancelled_result = _get_cancellation_result(cancel_event, debug)
    if cancelled_result is not None:
        _pending_confirmation = None
        _cancel_source_offer()
        _clear_non_web_focus()
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

    source_offer = _pending_source_read
    _pending_source_read = None
    pending = _pending_confirmation
    _pending_confirmation = None
    if pending is not None:
        request, executor, expires = pending
        answer = normalized_message.lower().rstrip(".!?")
        if answer in ("cancel", "no", "deny"):
            llm_service.clear_tool_focus()
            return CommandResult(handled=True, response_text="Action cancelled.")
        if answer in ("confirm", "yes", "approve"):
            if time.monotonic() >= expires:
                return CommandResult(handled=True, response_text="That confirmation expired. Please request the action again.")
            if request is None:
                return CommandResult(handled=True, response_text=executor(cancel_event))
            return _tool_outcome(request, executor.execute(request, cancel_event=cancel_event, confirmed=True), cancel_event=cancel_event, debug=debug)

    local_reply = action_command(normalized_message.rstrip(".!?")) or schedule_command(msg.strip())
    if local_reply is not None:
        response, confirm = local_reply
        if confirm is not None:
            _pending_confirmation = (None, confirm, time.monotonic() + 60)
        return CommandResult(handled=True, response_text=response, open_follow_up=confirm is not None)

    answer = normalized_message.casefold().rstrip('.!?')
    answer = ' '.join(answer.replace(',', ' ').split())
    answer = re.sub(r'^(?:(?:oh|um|uh|okay|ok)[,\s]+)+', '', answer)
    source_agreement = answer in ('yes', 'yeah', 'yep', 'sure', 'please do', 'go ahead', 'go for it',
                                 'yes please', 'yeah go for it', 'yes go ahead', 'sure go ahead')
    if source_offer is not None:
        if answer in ('no', 'no thanks', 'cancel', 'never mind', 'nevermind'):
            return CommandResult(True, response_text="Okay, I won't read that page.", open_follow_up=True)
        if source_agreement:
            current = source_seed(llm_service.get_tool_follow_up_context(), _mcp_catalog.manager) if _mcp_catalog else None
            if current != source_offer.seed:
                return CommandResult(True, response_text=MISSING_SOURCE, open_follow_up=True)
            return _execute_source_plan(source_offer, msg, cancel_event=cancel_event, debug=debug)
    elif source_agreement and _mcp_catalog is not None:
        if source_seed(llm_service.get_tool_follow_up_context(), _mcp_catalog.manager) is not None:
            return CommandResult(True, response_text="There isn't a pending page read. Say which source to read, such as read the first source.",
                                 open_follow_up=True)

    browser_args = browser_request(normalized_message)
    if browser_args is not None and _mcp_catalog is not None:
        from ai.intents.models import IntentRequest
        servers = [name for name,metadata in _mcp_catalog.manager.inspect().items()
                   if metadata.get('bundle') == 'browser' and metadata.get('enabled') and metadata.get('trusted')]
        if len(servers) != 1:
            return CommandResult(True,response_text='Choose one configured browser instance with an explicit tool command.')
        server = servers[0]
        _mcp_catalog.discover(server_id=server, cancel_event=cancel_event)
        try:
            executor = _mcp_catalog.manager.executor('mcp.'+server+'.inspect_page')
        except ValueError:
            return CommandResult(True,response_text='The browser tool is not configured or available.')
        request = IntentRequest(executor._intent,normalized_message,1,arguments=browser_args)
        return _tool_outcome(request,executor.execute(request,cancel_event=cancel_event),cancel_event=cancel_event,debug=debug)
    native_match = None if _intent_registry is None else IntentRouter(_intent_registry, strategies=(
        ExactMatchStrategy(), ExampleMatchStrategy(), CommandPrefixStrategy(),
    )).route(normalized_message)
    native_selected = native_match is not None and native_match.matched and native_match.intent.source == 'native'
    source_reply = None if native_selected else _web_sources_reply(normalized_message, cancel_event=cancel_event, debug=debug)
    if source_reply is not None:
        return source_reply
    filesystem_reply = None if native_selected else _filesystem_follow_up(
        normalized_message, commands, cancel_event=cancel_event, debug=debug)
    if filesystem_reply is not None:
        return filesystem_reply
    retained_reply = None if native_selected else _retained_tool_reply(normalized_message, cancel_event=cancel_event, debug=debug)
    if retained_reply is not None:
        return retained_reply

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
        if dispatch_result.tool_result is None or not dispatch_result.tool_result.success:
            llm_service.clear_tool_focus()
        return dispatch_result

    llm_service.clear_tool_focus()
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

        return CommandResult(handled=False, error=error)

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

    return CommandResult(
        handled=True,
        response_text=response_text,
        used_llm=True,
        open_follow_up=True,
    )


def clear_terminal():
    return os.system("cls")
