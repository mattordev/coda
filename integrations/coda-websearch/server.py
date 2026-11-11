
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from pathlib import Path
import argparse
import json

# Locate the config file alongside server.py
CONFIG_PATH = Path(__file__).resolve().parent / "websearch.config.json"

def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


server = MCPServer("coda-websearch", log_level="ERROR")


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
        return CallToolResult(
            content=[
                TextContent(type="text", text="Search query cannot be empty.")
            ],
            isError=True,
        )

    try:
        with DDGS(timeout=timeout) as client:
            results = client.text(
                query,
                max_results=max_results,
            )
    except DDGSException as error:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Web search failed: {type(error).__name__}: {error}"
                )
            ],
            isError=True,
        )

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

