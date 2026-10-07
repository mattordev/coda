"""Disposable real stdio server used only by local MCP integration tests."""

import os
from pathlib import Path
import sys
import time


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(str(os.getpid()), encoding="utf-8")
    if mode == "hang":
        time.sleep(60)
        return
    if mode == "malformed":
        print("private-malformed-token is not JSON", flush=True)
        time.sleep(60)
        return
    if mode == "disconnect":
        return

    import anyio
    from mcp.server import MCPServer

    server = MCPServer("coda-test-server", log_level="ERROR")

    if mode == "invalid_catalog":
        @server.tool(name="invalid tool name")
        def invalid_name() -> str:
            return "unused"

    @server.tool(structured_output=True)
    def echo(message: str) -> dict[str, object]:
        return {"message": message, "pid": os.getpid()}

    @server.tool()
    async def slow(delay: float = 60.0) -> dict:
        if len(sys.argv) > 3:
            Path(sys.argv[3]).write_text("started", encoding="utf-8")
        await anyio.sleep(delay)
        return {"finished": True}

    @server.tool()
    def domain_failure() -> dict:
        raise ValueError("private-domain-token")

    @server.tool()
    def disconnect_call() -> dict:
        os._exit(0)

    @server.tool()
    def malformed_call() -> dict:
        print("private-malformed-token is not JSON", flush=True)
        os._exit(0)

    @server.tool()
    def large_result(size: int = 10000) -> str:
        return "x" * size

    @server.tool()
    def environment() -> dict:
        return {
            "explicit": os.getenv("CODA_TEST_SECRET"),
            "unreferenced": os.getenv("CODA_TEST_OTHER_SECRET"),
        }

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
