"""Lightweight, bounded page text/metadata extraction; never execute page code."""

import codecs
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from utils.public_http import NetworkError

_VOID = frozenset('area base br col embed hr img input link meta param source track wbr'.split())
_IGNORE = frozenset('script style template svg canvas iframe object nav footer aside'.split())
_BLOCK = frozenset('address article aside blockquote div dl dt dd fieldset footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section table tr td th ul br'.split())
_ENCODINGS = frozenset(('utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'cp1252', 'iso8859-1', 'ascii'))
_TAG = re.compile(r'<(?:/?[a-zA-Z]|[!?])')
_OPAQUE = re.compile(r'<(script|style)(?=[\s/>])', re.IGNORECASE)
_OPAQUE_END = {tag: re.compile(r'</' + tag + r'\s*>', re.IGNORECASE) for tag in ('script', 'style')}


def _check_markup(text):
    """Enforce a token ceiling independent of HTMLParser patch-level repair rules."""
    offset = 0
    while (start := text.find('<', offset)) >= 0:
        if text.startswith('<!--', start):
            end = text.find('-->', start + 4)
            if end < 0:
                return
            offset = end + 3
            continue
        if not _TAG.match(text, start):
            offset = start + 1
            continue
        quote = None
        cursor = start + 1
        while cursor < len(text):
            if cursor - start >= 8192:
                raise NetworkError('resource_limit', 'The page contains oversized markup.')
            character = text[cursor]
            if quote:
                if character == quote:
                    quote = None
            elif character in ('"', "'"):
                quote = character
            elif character == '>':
                break
            cursor += 1
        offset = cursor + 1
        opaque = _OPAQUE.match(text, start)
        if opaque and cursor < len(text) and text[cursor - 1] != '/':
            end = _OPAQUE_END[opaque[1].lower()].search(text, offset)
            if end is None:
                return
            offset = end.end()


def _clean(text):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)


class _Text:
    def __init__(self, limit):
        self.limit, self.size, self.parts, self.truncated = limit, 0, [], False

    def add(self, text):
        text = re.sub(r'\s+', ' ', _clean(text))
        if not self.parts or self.parts[-1].endswith((' ', '\n')):
            text = text.lstrip()
        if not text:
            return
        room = self.limit - self.size
        if len(text) > room:
            self.truncated = True
        if room > 0:
            self.parts.append(text[:room])
            self.size += min(len(text), room)

    def line(self):
        if self.parts and not self.parts[-1].endswith('\n') and self.size < self.limit:
            self.parts.append('\n')
            self.size += 1

    def value(self):
        return re.sub(r' +\n', '\n', ''.join(self.parts)).strip()


@dataclass(frozen=True)
class _Frame:
    tag: str
    hidden: bool
    head: bool
    main: bool
    article: bool
    title: bool


class _Page(HTMLParser):
    def __init__(self, url, limit, max_links):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.body, self.main, self.article = (_Text(limit) for _ in range(3))
        self.title = _Text(240)
        self.description = ''
        self.language = ''
        self.base_url = url
        self.has_base = False
        self.links, self.max_links, self.links_truncated = [], max_links, False
        self.anchor = None

    def frame(self):
        return self.stack[-1] if self.stack else _Frame('', False, False, False, False, False)

    def buffers(self):
        frame = self.frame()
        if frame.hidden or frame.head or frame.title:
            return ()
        return (self.body,) + ((self.main,) if frame.main else ()) + ((self.article,) if frame.article else ())

    def _close_implicit(self, tag):
        if tag in _BLOCK and tag != 'br':
            paragraph = next((index for index in range(len(self.stack) - 1, -1, -1) if self.stack[index].tag == 'p'), None)
            if paragraph is not None:
                del self.stack[paragraph:]
        groups = {'li': ({'li'}, {'ul', 'ol'}), 'dt': ({'dt', 'dd'}, {'dl'}), 'dd': ({'dt', 'dd'}, {'dl'}),
                  'td': ({'td', 'th'}, {'tr'}), 'th': ({'td', 'th'}, {'tr'}), 'tr': ({'tr'}, {'table', 'tbody', 'thead'}),
                  'option': ({'option'}, {'select', 'datalist'})}
        if tag in groups:
            peers, containers = groups[tag]
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index].tag in containers:
                    break
                if self.stack[index].tag in peers:
                    del self.stack[index:]
                    break

    def handle_starttag(self, tag, attrs):
        if len(attrs) > 128:
            raise NetworkError('resource_limit', 'A page element exceeds the attribute limit.')
        self._close_implicit(tag)
        for buffer in self.buffers():
            if tag in _BLOCK:
                buffer.line()
        attrs = dict(attrs)
        parent = self.frame()
        style = (attrs.get('style') or '').lower()
        hidden = (parent.hidden or tag in _IGNORE or 'hidden' in attrs
                  or (attrs.get('aria-hidden') or '').lower() == 'true'
                  or bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', style)))
        frame = _Frame(tag, hidden, parent.head or tag == 'head', parent.main or tag == 'main',
                       parent.article or tag == 'article', parent.title or tag == 'title')
        if tag == 'html':
            language = attrs.get('lang') or ''
            if re.fullmatch(r'[a-zA-Z0-9-]{1,32}', language):
                self.language = language
        if tag == 'meta':
            name = (attrs.get('name') or attrs.get('property') or '').lower()
            if name in ('description', 'og:description') and not self.description:
                self.description = re.sub(r'\s+', ' ', _clean(attrs.get('content') or ''))[:500]
        if tag == 'base' and not self.has_base and attrs.get('href'):
            candidate = self._link(attrs['href'])
            if candidate:
                self.base_url = candidate
                self.has_base = True
        if tag == 'a' and not hidden and attrs.get('href'):
            self._finish_anchor()
            candidate = self._link(attrs['href'])
            if candidate:
                if len(self.links) >= self.max_links:
                    self.links_truncated = self.max_links > 0
                else:
                    self.anchor = candidate, _Text(240)
        if tag not in _VOID:
            if len(self.stack) >= 128:
                raise NetworkError('resource_limit', 'The page nesting exceeds its extraction limit.')
            self.stack.append(frame)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == 'a':
            self._finish_anchor()
        for buffer in self.buffers():
            if tag in _BLOCK:
                buffer.line()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        frame = self.frame()
        if frame.title and not frame.hidden:
            self.title.add(data)
        for buffer in self.buffers():
            buffer.add(data)
        if self.anchor is not None and not frame.hidden:
            self.anchor[1].add(data)

    def _link(self, value):
        if len(value) > 4096 or any(char.isspace() or ord(char) < 32 for char in value):
            return None
        try:
            candidate = urljoin(self.base_url, value)
            parsed = urlsplit(candidate)
            if len(candidate) <= 4096 and parsed.scheme in ('http', 'https') and parsed.hostname and parsed.username is None and parsed.password is None:
                return candidate
        except ValueError:
            pass
        return None

    def _finish_anchor(self):
        if self.anchor is not None:
            url, label = self.anchor
            if not any(link['url'] == url for link in self.links):
                self.links.append({'url': url, 'title': label.value()})
            self.anchor = None

    def feed_bounded(self, text):
        _check_markup(text)
        for offset in range(0, len(text), 4096):
            self.feed(text[offset:offset + 4096])
            if len(self.rawdata) > 8192:
                if self.cdata_elem in ('script', 'style'):
                    # Opaque data is intentionally omitted, retaining enough
                    # tail to recognise an end tag spanning feed boundaries.
                    self.rawdata = self.rawdata[-64:]
                elif self.rawdata.startswith('<!--'):
                    self.rawdata = '<!--' + self.rawdata[-64:]
                else:
                    raise NetworkError('resource_limit', 'The page contains oversized unfinished markup.')
        self.close()
        self._finish_anchor()


class _Charset(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        if tag != 'meta' or self.value:
            return
        attrs = dict(attrs)
        self.value = attrs.get('charset')
        if not self.value and (attrs.get('http-equiv') or '').lower() == 'content-type':
            message = Message()
            message['Content-Type'] = attrs.get('content') or ''
            self.value = message.get_content_charset()


def _decode(response, content_type):
    message = Message()
    message['Content-Type'] = content_type
    mime = message.get_content_type()
    if not content_type and response.body.lstrip().lower().startswith((b'<html', b'<!doctype html')):
        mime = 'text/html'
    if not (mime in ('text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown', 'application/json')
            or mime.startswith('application/') and mime.endswith('+json')):
        raise NetworkError('unsupported_content', 'That page format is not supported by the text reader.')
    encoding = message.get_content_charset()
    if response.body.startswith(codecs.BOM_UTF8):
        encoding = 'utf-8-sig'
    elif response.body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = 'utf-16'
    elif not encoding and mime in ('text/html', 'application/xhtml+xml'):
        probe = _Charset()
        probe.feed(response.body[:1024].decode('latin-1'))
        encoding = probe.value
    try:
        encoding = codecs.lookup(encoding or 'utf-8').name
        if encoding not in _ENCODINGS:
            raise LookupError('Unsupported web encoding.')
        decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
        text = decoder.decode(response.body, final=not response.partial)
    except (LookupError, UnicodeError) as error:
        raise NetworkError('page_invalid_encoding', 'The page did not contain supported valid text.') from error
    if '\0' in text:
        raise NetworkError('unsupported_content', 'Binary page content is not readable text.')
    return mime, encoding, text


def extract_page(response, *, max_chars=12000, max_links=10):
    """Return only bounded readable source observations, not the raw document."""
    if type(max_chars) is not int or not 1 <= max_chars <= 32000 or type(max_links) is not int or not 0 <= max_links <= 20:
        raise NetworkError('invalid_arguments', 'Supply bounded integer page text/link limits.')
    values = [value for name, value in response.headers if name.lower() == 'content-type']
    if len(set(values)) > 1 or any(len(value) > 1024 for value in values):
        raise NetworkError('unsupported_content', 'The page content type is ambiguous or oversized.')
    content_type = values[0] if values else ''
    mime, encoding, text = _decode(response, content_type)
    if not content_type and text.lstrip().lower().startswith(('<html', '<!doctype html')):
        mime = 'text/html'
    reasons = ['byte_limit'] if response.partial else []
    if response.status == 206:
        reasons.append('server_partial')
    title = description = language = ''
    links, links_truncated = [], False
    if mime in ('text/html', 'application/xhtml+xml'):
        parser = _Page(response.final_url, max_chars + 1, max_links)
        parser.feed_bounded(text)
        buffer, method = ((parser.main, 'html_main') if parser.main.value() else
                          (parser.article, 'html_article') if parser.article.value() else (parser.body, 'html_body'))
        text = buffer.value()
        if buffer.truncated or len(text) > max_chars:
            reasons.append('character_limit')
        title, description, language = parser.title.value(), parser.description, parser.language
        links, links_truncated = parser.links, parser.links_truncated
    elif mime in ('text/plain', 'text/markdown', 'application/json') or mime.startswith('application/') and mime.endswith('+json'):
        text = _clean(text).replace('\r\n', '\n').replace('\r', '\n')
        method = 'plain_text'
        if len(text) > max_chars:
            reasons.append('character_limit')
    else:
        raise NetworkError('unsupported_content', 'That page format is not supported by the text reader.')
    return {'kind': 'web_page', 'observation': 'fetched_text', 'requested_url': response.requested_url,
            'url': response.final_url, 'title': title, 'description': description, 'language': language,
            'content_type': mime, 'encoding': encoding, 'status': response.status, 'text': text[:max_chars],
            'partial': bool(reasons), 'partial_reasons': reasons, 'extraction_method': method,
            'bytes_read': len(response.body), 'response_bytes_received': response.wire_bytes,
            'redirects': response.redirects, 'links': links, 'links_truncated': links_truncated}
