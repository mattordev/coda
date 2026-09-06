import sys


def configure_stdout_encoding():
    """Keep LLM text readable when the process runs in Windows PowerShell."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
