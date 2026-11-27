"""Explicitly configured public-page reading over CODA's checked transport."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from utils.public_http import NetworkError, NetworkPolicy, get
from utils.web_extract import extract_page

CONFIG_PATH = Path(__file__).resolve().with_name('webread.config.json')
server = MCPServer('coda-webread', log_level='ERROR')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate configuration key.')
        result[key] = value
    return result


def _constant(_value):
    raise ValueError('Non-finite configuration value.')


def load_config():
    """Validate operator ceilings before any network access; never grant by default."""
    with CONFIG_PATH.open('rb') as file:
        raw = file.read(65537)
    if len(raw) > 65536:
        raise ValueError('Configuration is too large.')
    config = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(config, dict) or set(config) - {'network', 'extraction'}:
        raise ValueError('Invalid configuration fields.')
    network, extraction = config.get('network', {}), config.get('extraction', {})
    keys = {'enabled', 'allowed_hosts', 'allowed_ports', 'timeout_seconds', 'max_response_bytes',
            'max_wire_bytes', 'max_redirects'}
    if not isinstance(network, dict) or set(network) - keys or not isinstance(extraction, dict) or set(extraction) - {'max_chars', 'max_links'}:
        raise ValueError('Invalid configuration sections.')
    enabled = network.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('network.enabled must be a boolean.')
    hosts, ports = network.get('allowed_hosts', []), network.get('allowed_ports', [80, 443])
    if not isinstance(hosts, list) or not isinstance(ports, list):
        raise ValueError('Network allowlists must be arrays.')
    policy = NetworkPolicy(allowed_hosts=tuple(hosts), allowed_ports=tuple(ports),
                           **{key: value for key, value in network.items() if key not in ('enabled', 'allowed_hosts', 'allowed_ports')})
    chars, links = extraction.get('max_chars', 12000), extraction.get('max_links', 10)
    if type(chars) is not int or not 1 <= chars <= 32000 or type(links) is not int or not 0 <= links <= 20:
        raise ValueError('Invalid extraction limits.')
    return enabled, policy, chars, links


def failure(code, message):
    return CallToolResult(content=[TextContent(type='text', text=message)],
                          structuredContent={'kind': 'web_page', 'error': {'code': code, 'message': message}},
                          isError=True)


@server.tool(structured_output=False, annotations={'readOnlyHint': True, 'openWorldHint': True})
def read_url(url: str, max_chars: int | None = None, max_links: int | None = None,
             query: str | None = None) -> CallToolResult:
    """Read bounded text from one allowed public HTTP(S) URL; never execute page code."""
    try:
        snapshot = load_config()
    except (OSError, ValueError, TypeError):
        return failure('configuration_error', 'The webpage reader configuration is missing or invalid.')
    enabled, policy, ceiling_chars, ceiling_links = snapshot
    if not enabled:
        return failure('network_disabled', 'Public webpage reading is disabled in the configuration.')
    chars = ceiling_chars if max_chars is None else max_chars
    links = ceiling_links if max_links is None else max_links
    if type(chars) is not int or not 1 <= chars <= ceiling_chars or type(links) is not int or not 0 <= links <= ceiling_links:
        return failure('invalid_arguments', 'Requested text and link limits must fit the configured ceilings.')
    if query is not None and (not isinstance(query, str) or len(query) > 1000):
        return failure('invalid_arguments', 'Supply at most 1000 query characters.')

    def current():
        try:
            return load_config() == snapshot
        except (OSError, ValueError, TypeError):
            return False

    try:
        observed = get(url, policy, validate=current)
        page = extract_page(observed, max_chars=chars, max_links=links, query=query)
        if not current():
            return failure('network_denied', 'The webpage reader configuration changed during the request.')
        page['observed_at'] = datetime.now(timezone.utc).isoformat()
        label = 'Partial webpage text' if page['partial'] else 'Webpage text'
        readable = page['text'] or 'The page returned no readable text.'
        return CallToolResult(content=[TextContent(type='text', text=f"{label} from {page['url']}\n{readable}")],
                              structuredContent=page)
    except NetworkError as error:
        return failure(error.code, str(error))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    CONFIG_PATH = parser.parse_args().config.resolve()
    server.run(transport='stdio')
