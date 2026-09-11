"""Select update dependencies before importing the runtime."""

from pathlib import Path
import sys

from utils.update_bootstrap import (
    HANDOFF_ENV,
    redirect_to_selected_environment,
)
import os


def main():
    root = Path(__file__).resolve().parent
    # A supervised child must use its explicitly selected candidate environment,
    # not the previous committed selection while the parent checks readiness.
    if HANDOFF_ENV not in os.environ:
        exit_code = redirect_to_selected_environment(root, sys.argv[1:])
        if exit_code is not None:
            return exit_code

    from coda_runtime import main as run_runtime

    return run_runtime(skip_update_check=HANDOFF_ENV in os.environ)


if __name__ == "__main__":
    sys.exit(main())
