import os

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:
    find_dotenv = None
    load_dotenv = None


def _truthy(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def is_debug_enabled(default=False):
    configured_value = os.getenv("CODA_DEBUG")
    if configured_value is None:
        return default
    return _truthy(configured_value)


def set_debug_enabled(enabled):
    os.environ["CODA_DEBUG"] = "1" if enabled else "0"


def debug_print(*args, **kwargs):
    if is_debug_enabled(default=False):
        print(*args, **kwargs)


def reload_dotenv():
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
