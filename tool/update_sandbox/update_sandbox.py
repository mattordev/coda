"""Create and operate a disposable, non-Git CODA update installation."""

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time


TOOL_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = TOOL_ROOT.parents[1]
DEFAULT_SANDBOX = Path(tempfile.gettempdir()) / "coda-update-sandbox"
MANIFEST_NAME = "sandbox.json"
SEED_NAME = "seed.zip"
INSTALLATION_NAME = "installation"
PRESERVED_NAME = "preserved"
LAUNCHER_ENVIRONMENT = "launcher-environment"
LAUNCHER_LOG = "launcher-dependencies.log"
MANIFEST_FORMAT = 1
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
PRESERVED_FILES = (".env", "wakewords.json", "commands.json")


class SandboxProgress:
    """Dependency-free progress display used before Colorama is installed."""

    def __init__(self, label, estimate, stream=None):
        self.label = label
        self.estimate = estimate
        self.stream = sys.stdout if stream is None else stream
        self.interactive = self.stream.isatty()
        self.started = 0
        self.filled = 0
        self.stop = threading.Event()
        self.thread = None

    def _frame(self):
        elapsed = max(0.0, time.monotonic() - self.started)
        ratio = elapsed / self.estimate
        fraction = 0.9 * ratio if ratio <= 1 else 0.9 + 0.09 * (1 - 1 / ratio)
        self.filled = max(self.filled, min(19, int(fraction * 20)))
        bar = "#" * self.filled + "-" * (20 - self.filled)
        self.stream.write(
            f"\r\033[94m[{bar}]\033[0m {self.label} "
            f"({self._timing(elapsed)})"
        )
        self.stream.flush()

    def _timing(self, elapsed):
        colour = "\033[32;1m" if elapsed <= self.estimate else (
            "\033[33;1m" if elapsed <= self.estimate * 1.5 else "\033[31;1m"
        )
        return (
            f"{colour}{elapsed:.2f}s\033[0m elapsed / "
            f"~{self.estimate:g}s estimated"
        )

    def _animate(self):
        while not self.stop.wait(0.2):
            self._frame()

    def __enter__(self):
        self.started = time.monotonic()
        if self.interactive:
            self._frame()
            self.thread = threading.Thread(target=self._animate, daemon=True)
            self.thread.start()
        else:
            self.stream.write(f"[SANDBOX] {self.label}...\n")
        return self

    def __exit__(self, error_type, error, traceback):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        status = "done" if error_type is None else "stopped"
        if self.interactive:
            elapsed = max(0.0, time.monotonic() - self.started)
            bar = "#" * 20 if error_type is None else "#" * self.filled + "-" * (20 - self.filled)
            colour = "\033[92m" if error_type is None else "\033[91m"
            self.stream.write(
                f"\r\033[94m[{bar}]\033[0m {self.label} "
                f"({self._timing(elapsed)}) "
                f"{colour}{status}\033[0m\n"
            )
        else:
            self.stream.write(f"[SANDBOX] {self.label}: {status}\n")
        self.stream.flush()


def _run_launcher_step(command, label, estimate, timeout, environment, log, sandbox):
    log.write(f"\n--- {label} ---\n")
    log.flush()
    try:
        with SandboxProgress(label, estimate):
            subprocess.run(
                command, check=True, timeout=timeout, env=environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            f"{label} failed. Inspect {sandbox / LAUNCHER_LOG}; the sandbox installation is unchanged."
        ) from error


def _run_git(*arguments):
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _version(path):
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle).get("version")
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"A three-part version is required in {path}")
    return value


def _previous_patch(version):
    major, minor, patch = map(int, version.split("."))
    if patch == 0:
        raise ValueError("Cannot infer a previous patch version; enter one explicitly")
    return f"{major}.{minor}.{patch - 1}"


def _safe_sandbox(path, *, must_exist=False):
    sandbox = Path(path).expanduser().resolve(strict=must_exist)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if sandbox == temp_root or not sandbox.is_relative_to(temp_root):
        raise ValueError(f"Sandbox must be a dedicated directory inside {temp_root}")
    if not sandbox.name.startswith("coda-update-sandbox"):
        raise ValueError("Sandbox directory name must start with 'coda-update-sandbox'")
    return sandbox


def _manifest(sandbox):
    path = sandbox / MANIFEST_NAME
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT:
        raise ValueError(f"Not a recognised CODA update sandbox: {sandbox}")
    for name in ("from_version", "target_version", "target_commit"):
        if not isinstance(data.get(name), str):
            raise ValueError(f"Sandbox manifest is missing {name}")
    if not VERSION_PATTERN.fullmatch(data["from_version"]):
        raise ValueError("Sandbox starting version is invalid")
    if not VERSION_PATTERN.fullmatch(data["target_version"]):
        raise ValueError("Sandbox target version is invalid")
    if not COMMIT_PATTERN.fullmatch(data["target_commit"]):
        raise ValueError("Sandbox target commit is invalid")
    return data


def _write_start_version(installation, version):
    (installation / "version.json").write_text(
        json.dumps({"version": version}) + "\n", encoding="utf-8"
    )


def _extract_seed(sandbox, manifest):
    installation = sandbox / INSTALLATION_NAME
    shutil.unpack_archive(sandbox / SEED_NAME, installation)
    _write_start_version(installation, manifest["from_version"])
    preserved = sandbox / PRESERVED_NAME
    if preserved.is_dir():
        for name in PRESERVED_FILES:
            source = preserved / name
            if source.is_file():
                shutil.copy2(source, installation / name)
    return installation


def create(sandbox, from_version, target_version, target_commit, copy_config=False):
    sandbox = _safe_sandbox(sandbox)
    if sandbox.exists():
        raise FileExistsError(f"Sandbox already exists: {sandbox}. Reset, clean, or choose another path.")
    if not VERSION_PATTERN.fullmatch(from_version):
        raise ValueError("Starting version must use three parts, such as 1.4.3")
    if not VERSION_PATTERN.fullmatch(target_version):
        raise ValueError("Target version must use three parts, such as 1.4.4")
    if not COMMIT_PATTERN.fullmatch(target_commit):
        raise ValueError("Target commit must be a full lowercase 40-character SHA")
    _run_git("cat-file", "-e", f"{target_commit}^{{commit}}")

    sandbox.mkdir()
    try:
        _run_git("archive", "--format=zip", f"--output={sandbox / SEED_NAME}", "HEAD")
        manifest = {
            "format": MANIFEST_FORMAT,
            "from_version": from_version,
            "target_version": target_version,
            "target_commit": target_commit,
            "seed_commit": _run_git("rev-parse", "HEAD"),
            "configuration_copied": bool(copy_config),
        }
        (sandbox / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        if copy_config:
            preserved = sandbox / PRESERVED_NAME
            preserved.mkdir()
            for name in PRESERVED_FILES:
                source = REPOSITORY_ROOT / name
                if source.is_file():
                    shutil.copy2(source, preserved / name)
        _extract_seed(sandbox, manifest)
    except Exception:
        shutil.rmtree(sandbox, ignore_errors=True)
        raise
    print(f"Created sandbox: {sandbox}")
    print(f"Starting version: {from_version}; target: {target_version} at {target_commit}")
    if copy_config:
        print("Personal configuration was copied locally. Do not upload this sandbox.")
    return sandbox


def reset(sandbox):
    sandbox = _safe_sandbox(sandbox, must_exist=True)
    manifest = _manifest(sandbox)
    installation = sandbox / INSTALLATION_NAME
    if installation.exists():
        shutil.rmtree(installation)
    _extract_seed(sandbox, manifest)
    print(f"Reset sandbox to {manifest['from_version']}: {sandbox}")


def clean(sandbox):
    sandbox = _safe_sandbox(sandbox, must_exist=True)
    _manifest(sandbox)
    shutil.rmtree(sandbox)
    print(f"Removed sandbox and its generated environments: {sandbox}")


def status(sandbox):
    sandbox = _safe_sandbox(sandbox, must_exist=True)
    manifest = _manifest(sandbox)
    installation = sandbox / INSTALLATION_NAME
    current = _version(installation / "version.json") if installation.is_dir() else "missing"
    attempts = len(list(installation.glob(".coda-update-*"))) if installation.is_dir() else 0
    print(f"Sandbox: {sandbox}")
    print(f"Current version: {current}")
    print(f"Test path: {manifest['from_version']} -> {manifest['target_version']}")
    print(f"Target commit: {manifest['target_commit']}")
    print(f"Generated update attempts: {attempts}")
    print(f"Configuration copied: {manifest['configuration_copied']}")
    print(f"Launcher environment ready: {_launcher_python(sandbox).is_file()}")


def _launcher_python(sandbox):
    if os.name == "nt":
        return sandbox / LAUNCHER_ENVIRONMENT / "Scripts" / "python.exe"
    return sandbox / LAUNCHER_ENVIRONMENT / "bin" / "python"


def _same_interpreter(first, second):
    first = Path(first)
    second = Path(second)
    return first.parent.resolve() / first.name == second.parent.resolve() / second.name


def _prepare_launcher_environment(sandbox, requirements):
    environment = sandbox / LAUNCHER_ENVIRONMENT
    python = _launcher_python(sandbox)
    if environment.exists():
        if not _confirm("The launcher environment is incomplete. Delete and rebuild it?"):
            raise RuntimeError("Sandbox run cancelled; launcher environment is incomplete")
        shutil.rmtree(environment)

    process_environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        process_environment.pop(name, None)
    process_environment["PIP_CONFIG_FILE"] = os.devnull
    with (sandbox / LAUNCHER_LOG).open("w", encoding="utf-8") as log:
        _run_launcher_step(
            [sys.executable, "-I", "-m", "venv", "--copies", str(environment)],
            "Creating launcher environment", 30, 120, process_environment, log, sandbox,
        )
        process_environment["PATH"] = (
            str(python.parent) + os.pathsep + process_environment.get("PATH", "")
        )
        _run_launcher_step(
            [str(python), "-I", "-m", "pip", "--isolated", "--require-virtualenv", "install",
             "--disable-pip-version-check", "--no-input", "--no-user", "-r", str(requirements)],
            "Installing launcher dependencies", 300, 1200, process_environment, log, sandbox,
        )
        _run_launcher_step(
            [str(python), "-I", "-m", "pip", "--isolated", "--require-virtualenv", "check"],
            "Verifying launcher dependencies", 30, 120, process_environment, log, sandbox,
        )
    print("Sandbox launcher environment is ready.")
    return python


def run(sandbox):
    sandbox = _safe_sandbox(sandbox, must_exist=True)
    manifest = _manifest(sandbox)
    root = sandbox / INSTALLATION_NAME
    if not (root / "main.py").is_file():
        raise FileNotFoundError("Sandbox installation is missing; reset it before running")
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise ValueError("Refusing to test an installation inside a Git checkout")

    os.chdir(root)
    sys.path.insert(0, str(root))
    # Imports deliberately come from the disposable installation selected above.
    # pylint: disable=import-error,import-outside-toplevel
    from utils.update_bootstrap import launch_environment, selected_python, wait_for_process

    python = selected_python(root)
    current = Path(sys.executable)
    if python is not None and not _same_interpreter(python, current):
        child = subprocess.Popen(
            [str(python), str(Path(__file__).resolve()), "run", "--sandbox", str(sandbox)],
            cwd=root, env=launch_environment(python),
        )
        return wait_for_process(child)

    launcher = _launcher_python(sandbox)
    if python is None and not _same_interpreter(launcher, current):
        if not launcher.is_file():
            if not _confirm(
                "The sandbox needs an isolated launcher environment. Create it and install dependencies?"
            ):
                print("Sandbox run cancelled; no environments were changed.")
                return 0
            launcher = _prepare_launcher_environment(sandbox, root / "requirements.txt")
        child = subprocess.Popen(
            [str(launcher), str(Path(__file__).resolve()), "run", "--sandbox", str(sandbox)],
            cwd=root, env=launch_environment(launcher),
        )
        return wait_for_process(child)

    from utils import update_manager
    from utils.update_releases import Release, stable_version
    # pylint: enable=import-error,import-outside-toplevel

    release = Release(
        f"v{manifest['target_version']}", stable_version(manifest["target_version"]),
        manifest["target_commit"],
    )
    update_manager.latest_release = lambda: release
    print("SANDBOX TEST: a selected commit is used instead of a published latest release.")
    print(f"Only this installation can be updated: {root}")
    sys.argv = [str(root / "main.py"), "-m"]
    try:
        runpy.run_path(str(root / "main.py"), run_name="__main__")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            f"The Python running this tool is missing CODA dependency {error.name!r}. "
            "Run the sandbox tool with a fully provisioned CODA environment."
        ) from error
    except SystemExit as exit_code:
        return exit_code.code if isinstance(exit_code.code, int) else 0
    return 0


def _ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _confirm(prompt, default=False):
    answer = input(f"{prompt} (y/n): ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _choose_suggestion(prompt, suggested, value_name):
    answer = input(f"{prompt} {suggested}? (y/n or {value_name}): ").strip()
    if not answer or answer.lower() in ("y", "yes"):
        return suggested
    if answer.lower() in ("n", "no"):
        replacement = input(f"Enter {value_name}: ").strip()
        if not replacement:
            raise ValueError(f"A {value_name} is required")
        return replacement
    return answer


def wizard(sandbox):
    sandbox = _safe_sandbox(sandbox)
    print("CODA update sandbox wizard")
    print("This tool operates only on a disposable installation under your system temp directory.")
    if sandbox.exists():
        _manifest(sandbox)
        status(sandbox)
        action = _ask("Choose: run, reset, status, clean, or exit", "run").lower()
        if action == "run":
            if _confirm("Start the sandbox update test now?", True):
                return run(sandbox)
        elif action == "reset":
            if _confirm("Delete generated attempts and restore the pristine sandbox seed?"):
                reset(sandbox)
                if _confirm("Run the reset sandbox now?", True):
                    return run(sandbox)
        elif action == "status":
            return 0
        elif action == "clean":
            if _confirm(f"Permanently delete only {sandbox}?"):
                clean(sandbox)
        elif action != "exit":
            print("Unknown action; nothing changed.")
        return 0

    target_version = _version(REPOSITORY_ROOT / "version.json")
    target_commit = _run_git("rev-parse", "HEAD")
    from_version = _choose_suggestion(
        "Use disposable starting version", _previous_patch(target_version), "version number"
    )
    target_version = _choose_suggestion(
        "Use target update version", target_version, "version number"
    )
    target_commit = _choose_suggestion(
        "Use target commit", target_commit, "Commit SHA"
    )
    print(f"Sandbox destination: {sandbox}")
    copy_config = _confirm("Copy local .env and available command/wake-word configuration?")
    print("The target commit must already be reachable from GitHub before the update runs.")
    if not _confirm("Create this sandbox?", True):
        print("Nothing changed.")
        return 0
    create(sandbox, from_version, target_version, target_commit, copy_config)
    if _confirm("Run the update test now?", True):
        return run(sandbox)
    return 0


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", nargs="?", choices=("create", "run", "reset", "status", "clean"))
    result.add_argument("--sandbox", type=Path, default=DEFAULT_SANDBOX)
    result.add_argument("--from-version")
    result.add_argument("--target-version")
    result.add_argument("--target-commit")
    result.add_argument("--copy-config", action="store_true")
    return result


def main():
    arguments = parser().parse_args()
    if arguments.command is None:
        return wizard(arguments.sandbox)
    if arguments.command == "create":
        target_version = arguments.target_version or _version(REPOSITORY_ROOT / "version.json")
        create(
            arguments.sandbox,
            arguments.from_version or _previous_patch(target_version),
            target_version,
            arguments.target_commit or _run_git("rev-parse", "HEAD"),
            arguments.copy_config,
        )
        return 0
    return globals()[arguments.command](arguments.sandbox) or 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise SystemExit(f"Sandbox error: {error}") from error
