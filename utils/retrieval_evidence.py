"""Bounded query matching and extractive evidence; no model or network calls."""

import re
import heapq
from urllib.parse import urlsplit


_STOP = frozenset(('a an the this that these those what which how why when where does do is are can '
                  'could would should please research search web online explain compare summarise summarize '
                  'summary source sources selected first second third read using with from about for '
                  'and or of in on to it its me tell only webpage webpages page pages text contents '
                  'result results article excerpt observations observed').split())


def query_terms(query):
    query = re.sub(r'https?://\S+|\bsite:\S+', '', query, flags=re.IGNORECASE)
    expanded = re.sub(r'([a-z])([A-Z])|([A-Z])([A-Z][a-z])',
                      lambda match: ' '.join(part for part in match.groups() if part), query)
    words = re.findall(r'[a-z0-9_]{3,}', (query + ' ' + expanded).casefold())
    terms = list(dict.fromkeys(word for word in words if word not in _STOP))[:16]
    if any(word.startswith('limitation') for word in terms):
        terms += ['cannot', 'not', 'unsupported', 'malformed', 'invalid']
    return frozenset(terms)


def _score(text, terms):
    words = set(re.findall(r'[a-z0-9_]{3,}', text.casefold()))
    compact = re.sub(r'[^a-z0-9]', '', text.casefold())
    return sum(1 for term in terms if term in words or len(term) >= 6 and term in compact)


def ranked_source_urls(sources, query):
    """Prefer topical titles/paths, retaining stable provider order for ties."""
    terms = query_terms(query)
    sites = re.findall(r'\bsite:([a-z0-9.-]+)', query.casefold())[:3]
    ranked = []
    seen = set()
    for index, source in enumerate(sources[:10]):
        url = source['url']
        parsed = urlsplit(url)
        if sites and not any(parsed.hostname == site or (parsed.hostname or '').endswith('.' + site) for site in sites):
            continue
        if url in seen:
            continue
        seen.add(url)
        title, snippet = source.get('title', ''), source.get('snippet', '')
        score = 3 * _score(title, terms) + 3 * _score(parsed.path, terms) + _score(snippet, terms)
        if (not terms.intersection({'release', 'changelog', 'changes', 'history', 'new'})
                and re.search(r'whats?new|changelog|release[-_ ]notes', parsed.path + ' ' + title, re.IGNORECASE)):
            score -= 4
        ranked.append((-score, index, url))
    return [url for _score_value, _index, url in sorted(ranked)]


def _passages(text, query, *, width=400):
    terms = query_terms(query)
    pieces = []
    for index, match in enumerate(re.finditer(r'.+?(?:\n+|(?<=[.!?])\s+|$)', text, re.DOTALL)):
        paragraph = match[0].strip()
        if not paragraph or paragraph.startswith('[...'):
            continue
        if len(paragraph) > width:
            position = next((match.start() for match in re.finditer(r'\b\w+\b', paragraph)
                             if match[0].casefold() in terms), 0)
            start = max(0, position - width // 3)
            if start:
                boundary = paragraph.find(' ', start)
                start = boundary + 1 if boundary >= 0 else 0
            paragraph = paragraph[start:start + width]
            if len(paragraph) == width and ' ' in paragraph:
                paragraph = paragraph.rsplit(' ', 1)[0]
        heapq.heappush(pieces, (_score(paragraph, terms), -index, paragraph))
        if len(pieces) > 64:
            heapq.heappop(pieces)
    return [(score, -index, passage) for score, index, passage in pieces]


def query_excerpt(text, query, budget):
    """Select observed passages before clipping, and mark noncontiguous output."""
    if len(text) <= budget:
        return text
    pieces = _passages(text, query, width=min(800, budget))
    chosen, used = [], 0
    for score, index, passage in sorted(pieces, key=lambda item: (-item[0], item[1])):
        if chosen and score == 0:
            break
        cost = len(passage) + 28
        if used + cost > budget:
            continue
        chosen.append((index, passage))
        used += cost
        if len(chosen) == 10:
            break
    if not chosen:
        return text[:budget]
    return '\n[... omitted text ...]\n'.join(passage for _index, passage in sorted(chosen))[:budget]


def evidence_cards(data, question):
    """Fixed IDs point to literal retrieved passages, never generated claims."""
    if not isinstance(data, dict):
        return []
    page = data.get('structuredContent', data)
    if not isinstance(page, dict):
        return []
    single_page = page.get('kind') in ('web_page', 'browser_page')
    sources = [page] if single_page else page.get('sources')
    if not isinstance(sources, list):
        return []
    candidates = []
    terms = query_terms(question)
    for number, source in enumerate(sources[:3], 1):
        if not isinstance(source, dict) or not single_page and source.get('status') != 'fetched':
            continue
        if not isinstance(source.get('text'), str):
            continue
        for score, index, passage in _passages(source['text'], question):
            if score or not terms:
                candidates.append((-score, number, index, passage))
    cards, seen = [], set()
    for _score_value, number, _index, passage in sorted(candidates):
        if passage in seen:
            continue
        seen.add(passage)
        cards.append({'id': len(cards) + 1, 'source': number, 'text': passage})
        if len(cards) == 12:
            break
    return cards


def evidence_answer(cards, ids):
    """The model can select evidence IDs, but cannot introduce spoken facts."""
    if (not isinstance(ids, list) or len(ids) > 2
            or any(type(index) is not int or not 1 <= index <= len(cards) for index in ids)
            or len(set(ids)) != len(ids)):
        return None
    if not ids:
        return "The retrieved pages don't provide enough evidence to answer that question. Please choose a more specific source."
    return 'From the retrieved text: ' + ' '.join(cards[index - 1]['text'] for index in ids)
