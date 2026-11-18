
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
# Optional bundle dependency; discovery keeps this bundle unavailable until installed.
from ddgs import DDGS  # pylint: disable=import-error
from ddgs.exceptions import DDGSException, TimeoutException  # pylint: disable=import-error
from pathlib import Path
import argparse
import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

# Locate the config file alongside server.py
CONFIG_PATH = Path(__file__).resolve().parent / "websearch.config.json"

def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)
    if not isinstance(config, dict) or not isinstance(config.get("search", {}), dict):
        raise ValueError("Search configuration must be an object containing a search object.")
    search = config.get("search", {})
    for key, default, maximum in (("max_results", 5, 10), ("timeout", 10, 30)):
        value = search.setdefault(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"search.{key} must be an integer from 1 to {maximum}.")
    base_url = os.environ.get("SEARXNG_BASE_URL") or search.get("searxng_base_url", "")
    search["searxng_base_url"] = base_url
    if not isinstance(base_url, str):
        raise ValueError("search.searxng_base_url must be a string.")
    if base_url:
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or any(character.isspace() for character in base_url)
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError("search.searxng_base_url must be an HTTP(S) base URL without credentials, query or fragment.")
        # Accessing port validates malformed or out-of-range explicit ports.
        if parsed.port is not None and parsed.port == 0:
            raise ValueError("search.searxng_base_url must use a nonzero port.")
    return {"search": search}


server = MCPServer("coda-websearch", log_level="ERROR")


def search_failure(code: str, message: str, attempts: list | None = None) -> CallToolResult:
    """Keep a machine-readable reason and diagnostic evidence in the MCP result."""
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent={"kind": "web_search", "error": {"code": code, "message": message},
                           **({"attempts": attempts} if attempts else {})},
        isError=True,
    )


def normalize_pages(results: list, url_key: str, snippet_key: str, limit: int) -> list:
    """Keep usable HTTP(S) sources in the shared provider-independent contract."""
    pages = []
    for result in results:
        if not isinstance(result, dict):
            continue
        url = result.get(url_key)
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        pages.append({"title": result.get("title") if isinstance(result.get("title"), str) else "",
                      "url": url,
                      "snippet": result.get(snippet_key) if isinstance(result.get(snippet_key), str) else ""})
        if len(pages) >= limit:
            break
    return pages


def search_searxng(base_url: str, query: str, limit: int, timeout: int) -> list:
    """Query only the configured instance; result pages are never fetched."""
    url = f"{base_url.rstrip('/')}/search?{urlencode({'q': query, 'format': 'json'})}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "coda-websearch/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("SearXNG response must contain a results list.")
    return normalize_pages(payload["results"], "url", "content", limit)


@server.tool(structured_output=False)
def search_web(query: str, max_results: int | None = None) -> CallToolResult:
    """Search the web and return matching pages."""
    if not query.strip():
        return search_failure("invalid_arguments", "Search query cannot be empty.")
    try:
        config = load_config()
    except (OSError, ValueError) as error:
        return search_failure("unavailable", f"Web search configuration failed: {type(error).__name__}")
    search_config = config["search"]

    configured_max = search_config["max_results"]
    timeout = search_config["timeout"]

    if max_results is None:
        max_results = configured_max
    else:
        max_results = max(1, min(max_results, configured_max))

    attempts = []
    pages = []
    if search_config.get("searxng_base_url"):
        try:
            pages = search_searxng(search_config["searxng_base_url"], query, max_results, timeout)
            if not pages:
                attempts.append({"provider": "searxng", "code": "no_results", "message": "SearXNG returned no usable results."})
        except TimeoutError as error:
            attempts.append({"provider": "searxng", "code": "timeout", "message": f"SearXNG search timed out: {type(error).__name__}"})
        except (HTTPError, URLError, OSError, ValueError, HTTPException) as error:
            code = "timeout" if isinstance(getattr(error, "reason", None), TimeoutError) else "unavailable"
            attempts.append({"provider": "searxng", "code": code, "message": f"SearXNG search failed: {type(error).__name__}"})
    if not pages:
        return search_ddgs(query, max_results, timeout, attempts)
    return search_success(query, pages, "searxng")


def search_ddgs(query: str, max_results: int, timeout: int, attempts: list) -> CallToolResult:
    """Try the backup once, preserving both failures for diagnostic inspection."""
    try:
        with DDGS(timeout=timeout) as client:
            results = client.text(
                query,
                max_results=max_results,
            )
    except TimeoutException as error:
        code, message = "timeout", f"DDGS search failed: {type(error).__name__}: {error}"
    except DDGSException as error:
        # The base exception may mean an empty response, a blocked backend or
        # unusable parsed results. It does not establish that no matches exist.
        code, message = "unavailable", f"DDGS search failed: {type(error).__name__}: {error}"
    else:
        pages = normalize_pages(results or [], "href", "body", max_results)
        if pages:
            return search_success(query, pages, "ddgs")
        code, message = "no_results", "DDGS returned no usable search results."
    attempts.append({"provider": "ddgs", "code": code, "message": message})
    # Empty backup output cannot establish an empty search when the primary failed.
    final = next((attempt for attempt in reversed(attempts) if attempt["code"] != "no_results"), attempts[-1])
    return search_failure(final["code"], final["message"], attempts)


def search_success(query: str, pages: list, provider: str) -> CallToolResult:

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
            "provider": provider,
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

