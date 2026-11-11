
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
# Optional bundle dependency; discovery keeps this bundle unavailable until installed.
from ddgs import DDGS  # pylint: disable=import-error
from ddgs.exceptions import DDGSException, TimeoutException  # pylint: disable=import-error
from pathlib import Path
import argparse
import json

# Locate the config file alongside server.py
CONFIG_PATH = Path(__file__).resolve().parent / "websearch.config.json"

def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


server = MCPServer("coda-websearch", log_level="ERROR")


def search_failure(code: str, message: str) -> CallToolResult:
    """Keep a machine-readable reason and diagnostic evidence in the MCP result."""
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent={"kind": "web_search", "error": {"code": code, "message": message}},
        isError=True,
    )


@server.tool(structured_output=False)
def search_web(query: str, max_results: int | None = None) -> CallToolResult:
    """Search the web and return matching pages."""
    
    
    config = load_config()
    search_config = config["search"]

    configured_max = search_config["max_results"]
    timeout = search_config["timeout"]

    if max_results is None:
        max_results = configured_max
    else:
        max_results = max(1, min(max_results, configured_max))

    if not query.strip():
        return search_failure("invalid_arguments", "Search query cannot be empty.")

    try:
        with DDGS(timeout=timeout) as client:
            results = client.text(
                query,
                max_results=max_results,
            )
    except TimeoutException as error:
        return search_failure("timeout", f"DDGS search failed: {type(error).__name__}: {error}")
    except DDGSException as error:
        # The base exception may mean an empty response, a blocked backend or
        # unusable parsed results. It does not establish that no matches exist.
        return search_failure("unavailable", f"DDGS search failed: {type(error).__name__}: {error}")

    if not results:
        return search_failure("no_results", "DDGS returned no usable search results.")

    pages = [
        {
            "title": result.get("title", ""),
            "url": result.get("href", ""),
            "snippet": result.get("body", ""),
        }
        for result in results
    ]

    readable = "\n\n".join(
        f"{page['title']}\n{page['url']}\n{page['snippet']}"
        for page in pages
    )

    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=readable or "No results found.",
            )
        ],
        structuredContent={
            "kind": "web_search",
            "query": query,
            "results": pages,
        },
    )



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
    )

    args = parser.parse_args()
    CONFIG_PATH = args.config.resolve()

    server.run(transport="stdio")

