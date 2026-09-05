import os

_input_mode = "unknown"

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:
    find_dotenv = None
    load_dotenv = None


def _truthy(value: str|None) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


def is_debug_enabled(default: bool = False) -> bool:
    configured_value = os.getenv("CODA_DEBUG")
    if configured_value is None:
        return default
    return _truthy(configured_value)


def set_debug_enabled(enabled: bool) -> None:
    os.environ["CODA_DEBUG"] = "1" if enabled else "0"


def debug_print(*values: object) -> None:
    if is_debug_enabled(default=False):
        print(*values)


def reload_dotenv() -> tuple[bool, str]:
    if load_dotenv is None:
        return False, "python-dotenv is not installed"

    env_path = ""
    if find_dotenv is not None:
        env_path = find_dotenv(usecwd=True)

    if env_path:
        load_dotenv(env_path, override=True)
        return True, env_path

    load_dotenv(override=True)
    return True, ".env"

def set_input_mode(mode: str) -> None:
    global _input_mode
    _input_mode = mode
    
def get_input_mode() -> str:
    return _input_mode
