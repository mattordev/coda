"""Resolve explicitly selected first-party entrypoints without importing servers."""

from pathlib import Path
import sys


INSTALL_ROOT = Path(__file__).resolve().parents[2]
_ENTRYPOINTS = {"filesystem": Path("integrations/coda-filesystem/server.py")}


def bundled_launch(bundle_id):
    """Use the running interpreter and a fixed installed script, independent of cwd."""
    if not isinstance(bundle_id, str) or bundle_id not in _ENTRYPOINTS:
        raise ValueError("Unknown bundled MCP server.")
    root = INSTALL_ROOT.resolve()
    candidate = root / _ENTRYPOINTS[bundle_id]
    entrypoint = candidate.resolve()
    if not entrypoint.is_relative_to(root) or not entrypoint.is_file():
        raise ValueError("The bundled MCP entrypoint is missing or outside the installation.")
    if any(path.is_symlink() for path in (candidate, *candidate.parents) if path != root and path.is_relative_to(root)):
        raise ValueError("Bundled MCP entrypoints cannot use symbolic links.")
    return sys.executable, (str(entrypoint),)
