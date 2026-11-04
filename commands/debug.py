from ai.intents import Intent, IntentParameter, IntentRequest

import utils.llm_service as llm_service
import utils.on_command as command
import utils.runtime_state as runtime_state
import utils.speak_response as speak_response
import utils.stt_service as stt_service
from runtime import model_warmup


INTENT = Intent(
    name="debug",
    description="View or change CODA's runtime debugging configuration.",
    parameters=(
        IntentParameter(
            name="action",
            description="The debug action: on, off, status, or reload.",
            required=False,
        ),
    ),
    aliases=(
        "debug mode",
        "diagnostics",
        "diagnostic mode",
    ),
)


def _get_action(message: str) -> str:
    tokens = message.lower().split()[1:]

    if tokens and tokens[0] == "mode":
        tokens = tokens[1:]

    if not tokens:
        return "status"

    if tokens[0] in ("config", "env") and len(tokens) > 1:
        return tokens[1]

    return tokens[0]


def _print_status():
    debug_enabled = runtime_state.is_debug_enabled(default=False)
    print(f"Debug mode is {'enabled' if debug_enabled else 'disabled'}.")
    print(f"LLM fallback is {llm_service.describe_llm_fallback()}.")
    print(f"Speech-to-text is {stt_service.describe_stt_provider()}.")
    for subsystem, status in model_warmup.snapshot().items():
        duration = status["duration_seconds"]
        elapsed = "" if duration is None else f" ({duration:.2f}s)"
        print(f"Pre-warm {subsystem}: {status['state']}{elapsed}")


def _reload_runtime_config():
    model_warmup.shutdown()
    dotenv_loaded, source = runtime_state.reload_dotenv()
    command.reload_config()
    speak_response.reload_config(check_availability=False)
    stt_service.reload_config()
    model_warmup.start()

    if dotenv_loaded:
        print(f"Reloaded configuration from {source}.")
    else:
        print(f"Configuration reload was limited: {source}")

    _print_status()
    return True


def run(request: IntentRequest) -> bool:
    action = _get_action(request.message)

    if action in ("on", "enable", "enabled", "true", "1"):
        runtime_state.set_debug_enabled(True)
        _print_status()
        return True

    if action in ("off", "disable", "disabled", "false", "0"):
        runtime_state.set_debug_enabled(False)
        _print_status()
        return True

    if action in ("status", "show"):
        _print_status()
        return True

    if action in ("reload", "refresh"):
        return _reload_runtime_config()

    print("Usage: debug [on|off|status|reload]")
    return False
