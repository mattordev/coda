"""Bounded source selection from user requests and host-proven search observations."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from ai.mcp import bundled
from ai.tools.result_summary import _payload
from ai.intents.models import IntentRequest
from ai.tools.models import ToolError, ToolResult
from utils.retrieval_evidence import ranked_source_urls

SOURCE_SECONDS = 120
MISSING_SOURCE = 'Please choose a current search result or provide its full URL; that source reference is missing, expired or ambiguous.'
_ORDINALS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
             'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10}


def source_environment_token(definition):
    """Fingerprint actual exported references, including operator-chosen aliases."""
    environment = json.dumps(definition.environment(), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(environment.encode('utf-8')).hexdigest()


def source_configuration(definition):
    """Read bounded operator configuration without importing or launching a server."""
    identity = getattr(definition, 'bundled_server', None)
    tool = {'websearch': 'search_web', 'webread': 'read_url'}.get(identity)
    if (tool is None or not definition.enabled or not definition.trusted
            or tool not in definition.allowed_tools or definition.bundle_error):
        return None
    bundle = bundled.discover_bundles().bundles.get(identity)
    if bundle is None or bundle.error:
        return None
    settings = bundle.manifest.get('defaults', {}) | json.loads(definition.settings_json)
    configuration = bundle.manifest['configuration_files'][0]
    path = Path(settings.get(configuration['setting'], bundle.root / configuration['default']))
    with path.open('rb') as file:
        raw = file.read(65537)
    if len(raw) > 65536:
        return None
    config = json.loads(raw)
    if not isinstance(config, dict):
        return None
    if identity == 'webread' and (not isinstance(config.get('network'), dict) or config['network'].get('enabled') is not True):
        return None
    fingerprint = hashlib.sha256(raw + b'\0' + source_environment_token(definition).encode('ascii')).hexdigest()
    return fingerprint, config


def _source_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(character.isspace() or ord(character) < 32 for character in url):
        return False
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and parsed.username is None and parsed.password is None
    except ValueError:
        return False


def source_seed(result, manager):
    """Protocol fields alone never establish reference provenance or extend expiry."""
    if result is None or not result.success or not isinstance(result.host_context, dict):
        return None
    context = result.host_context
    seed = context.get('source_seed')
    if seed is None:
        payload = _payload(result.data)
        if context.get('bundle') != 'websearch' or context.get('tool') != 'search_web' or not isinstance(payload, dict) or payload.get('kind') != 'web_search':
            return None
        results = payload.get('results')
        if not isinstance(results, list) or len(results) > 10:
            return None
        sources = []
        for number, entry in enumerate(results, 1):
            if not isinstance(entry, dict) or not _source_url(entry.get('url')):
                return None
            sources.append({'number': number, 'url': entry['url'],
                            'title': str(entry.get('title', ''))[:300],
                            'snippet': str(entry.get('snippet', ''))[:2000],
                            'observation': 'search_snippet'})
        seed = {'server': context.get('server'), 'token': context.get('token'),
                'observed_at': context.get('observed_at'), 'sources': sources}
    try:
        now = time.monotonic()
        observed = seed['observed_at']
        token = manager.source_focus_token(seed['server'])
        if not observed <= now < observed + SOURCE_SECONDS or token is None or token != seed['token']:
            return None
        for server, expected in seed.get('readers', {}).items():
            if manager.source_focus_token(server) != expected:
                return None
    except (KeyError, TypeError, ValueError):
        return None
    return deepcopy(seed)


@dataclass(frozen=True)
class SourcePlan:
    kind: str
    urls: tuple[str, ...] = ()
    query: str = ''
    seed: dict | None = None
    reply: str = ''


def plan_sources(message, previous, manager):
    """Recognise a fixed read-only grammar; never interpret page prose as actions."""
    if not isinstance(message, str) or len(message) > 5000:
        return None
    text = message.strip()
    # Accept a small spoken wrapper only when the remaining request matches
    # the fixed source grammar. Never derive actions from arbitrary dialogue.
    text = re.sub(r'^(?:(?:um|uh|err|oh|okay|ok)[,\s]+)+', '', text, flags=re.IGNORECASE)
    wrapped = re.fullmatch(r'use the search results? about .{1,1000}? and ((?:read|open|fetch|summari[sz]e|compare) .+)',
                          text, re.IGNORECASE)
    if wrapped:
        text = wrapped[1]
    research = re.fullmatch(r'research(?: (?:this question|the following):)?\s+(.{1,1000})', text, re.IGNORECASE)
    if research is None:
        research = re.fullmatch(r'(?:explain|compare) (.{1,1000}?) (?:using|with) (?:web |online )?(?:sources|source pages|webpages)[.!?]?', text, re.IGNORECASE)
    if research:
        if re.search(r'https?://', research[1], re.IGNORECASE):
            urls = tuple(dict.fromkeys(re.findall(r'https?://[^\s<>"\[\]]+', research[1], re.IGNORECASE)))
            if not 1 <= len(urls) <= 3 or not all(_source_url(url) for url in urls):
                return SourcePlan('clarify', reply='Please choose at most three ordinary public source URLs without credentials.')
            return SourcePlan('read', urls=urls)
        return SourcePlan('research', query=research[1])
    snippets = re.fullmatch(r'(?:explain|summari[sz]e) (?:the )?(?:search )?snippets(?: only)?[.!?]?', text, re.IGNORECASE)
    if snippets:
        seed = source_seed(previous, manager)
        return SourcePlan('snippets', seed=seed) if seed else SourcePlan('clarify', reply=MISSING_SOURCE)
    action = re.fullmatch(r'(read|open|fetch|summari[sz]e|compare)\s+(.+)', text, re.IGNORECASE)
    if not action:
        return None
    selection = action[2].strip()
    if 'http://' in selection.lower() or 'https://' in selection.lower():
        urls = re.findall(r'https?://[^\s<>"\[\]]+', selection, re.IGNORECASE)
        remaining = selection
        for url in urls:
            remaining = remaining.replace(url, '', 1)
        if not re.fullmatch(r'[\s,:;]*(?:(?:these|this|the|three|two|websites?|webpages?|pages?|articles?|sources?|at|from|and|versus|vs|summari[sz]e|explain|it|them)[\s,:;]*)*', remaining, re.IGNORECASE):
            return SourcePlan('clarify', reply='Please choose explicit URLs or current numbered results separately; do not mix old references with new URLs.')
        urls = list(dict.fromkeys(urls))
        if not 1 <= len(urls) <= 3:
            return SourcePlan('clarify', reply='Please choose at most three source URLs for one comparison.')
        if not all(_source_url(url) for url in urls):
            return SourcePlan('clarify', reply='Please provide ordinary absolute HTTP or HTTPS source URLs without credentials.')
        seed = source_seed(previous, manager)
        if seed and not all(url in {source['url'] for source in seed['sources']} for url in urls):
            seed = None
        return SourcePlan('read', urls=tuple(urls), seed=seed)
    normalized = selection.casefold().rstrip('.!?')
    tokens = re.findall(r'\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|[0-9]+)\b', normalized)
    leftover = re.sub(r'\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|[0-9]+|the|and|with|versus|vs|sources?|results?|websites?|pages?)\b|[\s,;:]', '', normalized)
    named = re.fullmatch(r'(?:the )?(?:source|result|page)(?: (?:named|titled))? ["\u201c](.{1,300})["\u201d]', selection.rstrip('.!?'), re.IGNORECASE)
    if not (tokens and not leftover or named):
        return None
    seed = source_seed(previous, manager)
    if seed is None:
        context = previous.host_context if previous is not None else None
        web_context = isinstance(context, dict) and (context.get('bundle') == 'websearch' or 'source_seed' in context)
        if not named and not re.search(r'\b(?:sources?|results?|websites?|pages?)\b', normalized) and not web_context:
            return None
        return SourcePlan('clarify', reply=MISSING_SOURCE)
    if named:
        matches = [entry for entry in seed['sources'] if entry['title'].casefold() == named[1].casefold()]
        indices = [entry['number'] for entry in matches] if len(matches) == 1 else []
    else:
        if any(token.isdigit() and len(token) > 2 for token in tokens):
            return SourcePlan('clarify', reply=MISSING_SOURCE)
        indices = list(dict.fromkeys(_ORDINALS[token] if token in _ORDINALS else int(token) for token in tokens))
    if len(indices) > 3:
        return SourcePlan('clarify', reply='Please choose at most three current sources for one comparison.')
    if not indices or any(not 1 <= index <= len(seed['sources']) for index in indices):
        return SourcePlan('clarify', reply=MISSING_SOURCE)
    return SourcePlan('read', urls=tuple(seed['sources'][index - 1]['url'] for index in indices), seed=seed)


class _Budget:
    def __init__(self, external):
        self.external = external
        self.deadline = time.monotonic() + 60

    def is_set(self):
        return (self.external is not None and self.external.is_set()) or time.monotonic() >= self.deadline


def _failure(code, message):
    return ToolResult(False, error=ToolError(code, message))


def _instance(manager, identity, preferred=None):
    candidates = []
    for name, runtime in tuple(manager._servers.items()):
        if getattr(runtime.definition, 'bundled_server', None) == identity and manager.source_focus_token(name) is not None:
            candidates.append(name)
    if preferred is not None:
        return preferred if preferred in candidates else None
    return candidates[0] if len(candidates) == 1 else None


def _invoke(catalog, server, name, arguments, message, budget):
    if budget.is_set():
        return _failure('cancelled', 'Source retrieval was cancelled or reached its deadline.')
    intents = catalog.discover(server_id=server, cancel_event=budget)
    intent = next((intent for intent in intents if intent.name == f'mcp.{server}.{name}'), None)
    if intent is None:
        return _failure('tool_not_found', 'The selected source tool is unavailable.')
    request = IntentRequest(intent, message, 1.0, 'web_sources', arguments)
    try:
        executor = catalog.manager.executor(intent.name, cancel_event=budget)
    except ValueError:
        return _failure('tool_not_found', 'The selected source tool is unavailable.')
    return executor.execute(request, cancel_event=budget)


def run_sources(plan, catalog, message, *, cancel_event=None):
    """Execute one fixed, bounded read-only workflow through normal validated tools."""
    manager = catalog.manager
    if plan.kind == 'clarify':
        return None, plan.reply
    seed = deepcopy(plan.seed)
    if plan.kind == 'snippets':
        data = {'kind': 'web_sources', 'scope': 'search_snippets_only', 'sources': seed['sources'],
                'partial': False, 'page_attempts': 0}
        return ToolResult(True, {'structuredContent': data}, host_context={'source_seed': seed}), None
    budget = _Budget(cancel_event)
    if budget.is_set():
        return _failure('cancelled', 'Source retrieval was cancelled.'), None
    if plan.kind == 'research':
        server = _instance(manager, 'websearch')
        if server is None:
            return None, 'Please configure one permitted web-search instance for this research request.'
        search = _invoke(catalog, server, 'search_web', {'query': plan.query}, message, budget)
        if not search.success:
            return search, None
        seed = source_seed(search, manager)
        if seed is None:
            return _failure('permission_denied', 'The search source provenance or current permissions could not be verified.'), None
        urls = tuple(ranked_source_urls(seed['sources'], plan.query))[:2]
    else:
        urls = plan.urls
    if len(urls) > 3:
        return None, 'Please choose at most three source pages.'
    if not urls:
        return _failure('no_results', 'No usable source pages were returned.'), None
    server = _instance(manager, 'webread', seed.get('reader_server') if seed else None)
    if server is None:
        return None, 'Please configure one permitted webpage-reader instance, or provide an explicit MCP reader command.'
    reader_token = manager.source_focus_token(server)
    try:
        configured = source_configuration(manager._servers[server].definition)
        ceiling = configured[1].get('extraction', {}).get('max_chars', 12000)
        if type(ceiling) is not int or not 1 <= ceiling <= 32000:
            raise ValueError('Invalid text ceiling.')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return _failure('configuration_error', 'The selected webpage reader configuration is invalid.'), None
    chars = min(5000, 10000 // len(urls), ceiling)
    observations = []
    for url in urls:
        if budget.is_set():
            break
        if manager.source_focus_token(server) != reader_token:
            return _failure('permission_denied', 'Webpage reader permissions changed during source retrieval.'), None
        arguments = {'url': url, 'max_chars': chars, 'max_links': 0}
        if plan.kind == 'research':
            arguments['query'] = plan.query
        outcome = _invoke(catalog, server, 'read_url', arguments, message, budget)
        if manager.source_focus_token(server) != reader_token:
            return _failure('permission_denied', 'Webpage reader permissions changed during source retrieval.'), None
        payload = _payload(outcome.data)
        if (outcome.success and not budget.is_set() and isinstance(payload, dict)
                and payload.get('kind') == 'web_page' and payload.get('observation') == 'fetched_text'
                and isinstance(payload.get('text'), str) and _source_url(payload.get('url'))):
            page = {key: deepcopy(payload[key]) for key in (
                'url', 'title', 'text', 'partial', 'partial_reasons', 'observed_at', 'observation', 'extraction_method') if key in payload}
            if len(page['text']) > chars:
                page['text'] = page['text'][:chars]
                page['partial'] = True
                page['partial_reasons'] = ['workflow_character_limit']
            page.update(requested_url=url, status='fetched')
            observations.append(page)
        else:
            error = payload.get('error') if isinstance(payload, dict) else None
            code = error.get('code') if isinstance(error, dict) else outcome.error.code if outcome.error else 'invalid_tool_result'
            if budget.is_set():
                code = 'network_timeout'
            if not isinstance(code, str) or not re.fullmatch(r'[a-z_]{1,64}', code):
                code = 'unknown_error'
            observations.append({'requested_url': url, 'status': 'failed', 'error': {'code': code}, 'observation': 'unavailable'})
    if cancel_event is not None and cancel_event.is_set():
        return _failure('cancelled', 'Source retrieval was cancelled.'), None
    if seed is not None:
        if manager.source_focus_token(seed['server']) != seed['token']:
            return _failure('permission_denied', 'Search permissions changed during source retrieval.'), None
        seed.setdefault('readers', {})[server] = reader_token
        seed['reader_server'] = server
    partial = len(observations) != len(urls) or any(
        page['status'] != 'fetched' or page.get('partial') is True for page in observations)
    data = {'kind': 'web_sources', 'scope': 'fetched_pages', 'sources': observations, 'partial': partial,
            'page_attempts': len(observations), 'requested_pages': len(urls),
            'not_attempted': len(urls) - len(observations)}
    # One workflow snapshot in the existing result history; no intermediate duplicates.
    return ToolResult(True, {'structuredContent': data}, host_context={'source_seed': seed} if seed else None), None
