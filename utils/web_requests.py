"""Narrow spoken web conveniences; transport policy remains authoritative."""
import re


def normalize_spoken(message):
    text = message.strip()
    text = re.sub(r'^(?:(?:um|uh|okay|ok)[,\s]+)+', '', text, flags=re.I)
    text = re.sub(r'^(?:coda|coder)[,\s]+', '', text, flags=re.I)
    text = re.sub(r'^(?:go to|code of)\s+(?=summari[sz]e|research|read\b)', '', text, flags=re.I)
    text = re.sub(r'^summarized\b', 'summarise', text, flags=re.I)
    return text


def public_url(value):
    value = value.strip()
    value = re.sub(r'\s+dot\s+', '.', value, flags=re.I)
    value = re.sub(r'^https?\s+(?:colon|semicolon)\s+(?:forward\s+)?slash\s+(?:forward\s+)?slash\s*', 'https://', value, flags=re.I)
    if re.fullmatch(r'(?:[a-z0-9-]+\.)+[a-z]{2,}\.?(?::[0-9]{1,5})?(?:/[^\s\\@]*)?', value, re.I):
        value = 'https://' + value
    return value


def browser_request(message):
    text = normalize_spoken(message)
    patterns = (
        (r'(?:use the browser to inspect|inspect in the browser) (.+)', False, None),
        (r'(?:take a browser screenshot of|take a screenshot of) (.+)', True, None),
        (r'use the browser to extract the page title from (.+)', False, {'title':'title'}),
    )
    for pattern, capture, selectors in patterns:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            url = public_url(match[1].rstrip('.!?'))
            if not re.fullmatch(r'https?://[^\s]+', url, re.I):
                return None
            result = {'url':url, 'capture':capture}
            if selectors:
                result['selectors'] = selectors
            return result
    return None
