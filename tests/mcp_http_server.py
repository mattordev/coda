"""Disposable authenticated loopback MCP server; never a real integration."""

import os
import json
from pathlib import Path
import socket
import sys

import anyio
from mcp.server import MCPServer
from starlette.responses import JSONResponse, PlainTextResponse
import uvicorn


def main():
    ready, mode, actions = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    server = MCPServer("coda-http-test", log_level="ERROR")
    counter = 0
    initializations = 0

    @server.tool(structured_output=True)
    def increment() -> dict[str, int]:
        nonlocal counter
        counter += 1
        return {"count": counter, "initializations": initializations}

    @server.tool(structured_output=True)
    def echo(message: str) -> dict[str, str]:
        return {"message": message}

    @server.tool()
    async def slow(delay: float = 60) -> dict:
        actions.write_text("started", encoding="utf-8")
        await anyio.sleep(delay)
        return {"finished": True}

    @server.tool()
    def disconnect_call() -> dict:
        with actions.open("a", encoding="utf-8") as record:
            record.write("accepted\n")
        os._exit(0)

    @server.tool()
    def large_result(size: int = 10000) -> str:
        return "x" * size

    @server.tool()
    def domain_failure() -> dict:
        raise ValueError("private-domain-token")

    app = server.streamable_http_app(json_response=mode != "sse")

    async def authenticated(scope, receive, send):
        nonlocal initializations
        if scope["type"] != "http":
            return await app(scope, receive, send)
        headers = dict(scope["headers"])
        expected = os.environ.get("CODA_REMOTE_TEST_TOKEN", "Bearer private-test-token").encode()
        if headers.get(b"authorization") != expected:
            return await JSONResponse({"error": "private-auth-token"}, status_code=401)(scope, receive, send)
        if mode == "hang":
            await anyio.sleep(60)
        if mode == "malformed":
            return await PlainTextResponse("private-malformed-token", media_type="application/json")(scope, receive, send)
        if mode == "too_large":
            return await PlainTextResponse("private-limit-token", status_code=413)(scope, receive, send)
        buffered = []
        if scope["method"] == "POST":
            body = bytearray()
            while True:
                message = await receive()
                buffered.append(message)
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            payload = json.loads(body)
            if payload.get("method") in ("initialize", "server/discover"):
                initializations += 1
            # Exercise the SDK's supported legacy negotiation within Streamable
            # HTTP: unlike modern JSON replies, these responses use SSE frames.
            if mode == "sse" and payload.get("method") == "server/discover":
                return await JSONResponse({
                    "jsonrpc":"2.0", "id":payload["id"],
                    "error":{"code":-32601,"message":"Method not found"},
                })(scope, receive, send)

        async def replay_receive():
            return buffered.pop(0) if buffered else await receive()

        return await app(scope, replay_receive, send)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    ready.write_text(str(port), encoding="utf-8")
    config = uvicorn.Config(authenticated, log_level="critical", access_log=False, lifespan="on")
    uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
