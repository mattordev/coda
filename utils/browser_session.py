"""Disposable offline rendering; all permitted HTTP is brokered by CODA.

No Playwright import, installation, browser launch or network access at import.
The public API exposes bounded operations, never caller-supplied JavaScript.
"""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from threading import Event
from time import monotonic

from utils.public_http import NetworkError, destination, get
from utils.browser_forms import FORM_SNAPSHOT, proposal, validate_fields


@dataclass(frozen=True)
class BrowserLimits:
    timeout_seconds: float = 25
    max_requests: int = 24
    max_total_bytes: int = 2097152
    max_total_wire_bytes: int = 4194304
    max_chars: int = 12000
    max_elements: int = 20
    max_screenshot_bytes: int = 524288

    def __post_init__(self):
        bounds = {'timeout_seconds': (1, 60), 'max_requests': (1, 64),
                  'max_total_bytes': (1024, 8388608), 'max_total_wire_bytes': (1024, 16777216),
                  'max_chars': (1, 32000), 'max_elements': (1, 50),
                  'max_screenshot_bytes': (1024, 1048576)}
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) not in (int, float) or not low <= value <= high or (
                    name != 'timeout_seconds' and type(value) is not int):
                raise ValueError('Invalid browser limit: ' + name)


class ResourceBroker:
    """Serialize bounded GETs so concurrent subresources share one authority/budget."""

    def __init__(self, policy, limits, *, validate=None, cancel_event=None):
        self.policy, self.limits = policy, limits
        self.validate = validate
        self.cancel = cancel_event or Event()
        self.deadline = monotonic() + limits.timeout_seconds
        self.requests = self.bytes = self.wire_bytes = self.blocked = 0
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.reasons = set()

    def check(self):
        if self.cancel.is_set():
            raise NetworkError('cancelled', 'Browser work was cancelled.')
        if self.validate is not None and not self.validate():
            raise NetworkError('permission_denied', 'Browser permissions changed during the operation.')
        if monotonic() >= self.deadline:
            raise NetworkError('network_timeout', 'The browser operation reached its total time limit.')

    async def response(self, url, method='GET', *, follow_redirects=False):
        async with self.lock:
            self.check()
            if method != 'GET':
                raise NetworkError('action_denied', 'Browser reading cannot submit or mutate external data.')
            destination(url, self.policy)
            if self.requests >= self.limits.max_requests or self.bytes >= self.limits.max_total_bytes or self.wire_bytes >= self.limits.max_total_wire_bytes:
                raise NetworkError('resource_limit', 'The browser resource budget was exhausted.')
            if self.limits.max_total_wire_bytes - self.wire_bytes < 1024:
                raise NetworkError('resource_limit', 'The remaining browser wire budget is too small for another response.')
            self.requests += 1
            policy = replace(self.policy,
                             timeout_seconds=min(self.policy.timeout_seconds, self.deadline - monotonic()),
                             max_response_bytes=min(self.policy.max_response_bytes, self.limits.max_total_bytes - self.bytes),
                             max_wire_bytes=min(self.policy.max_wire_bytes, self.limits.max_total_wire_bytes - self.wire_bytes),
                             max_redirects=min(self.policy.max_redirects, self.limits.max_requests - self.requests))
            # A shield keeps cancellation from losing ownership of a running
            # native socket worker. Its Event closes it at the next guard check.
            task = asyncio.create_task(asyncio.to_thread(get, url, policy, cancel_event=self.cancel,
                                                        validate=self.validate, follow_redirects=follow_redirects))
            self.tasks.add(task)
            try:
                result = await asyncio.shield(task)
            finally:
                if task.done():
                    self.tasks.discard(task)
            self.bytes += len(result.body)
            self.wire_bytes += result.wire_bytes
            self.requests += result.redirects
            self.check()
            if 300 <= result.status < 400:
                # Playwright does not re-route every redirect hop. Never send
                # redirects into the browser's own network stack.
                raise NetworkError('redirect_limit', 'Redirected browser subresources are not supported.')
            if result.partial:
                # Executing a truncated script/document can give a misleading
                # or unsafe observation. Discard this resource completely.
                raise NetworkError('resource_limit', 'A browser resource exceeded its response limit.')
            return result

    async def route(self, route):
        request = route.request
        try:
            # No ambient browser headers, cookies, auth or request bodies cross
            # this boundary. No route.continue_ or route.fetch fallback exists.
            result = await self.response(request.url, request.method)
            headers = {name.lower(): value for name, value in result.headers
                       if name.lower() in ('content-type', 'location', 'content-security-policy',
                                           'x-content-type-options', 'access-control-allow-origin')}
            await route.fulfill(status=result.status, headers=headers, body=result.body)
        except NetworkError as error:
            self.blocked += 1
            self.reasons.add(error.code)
            await route.abort('blockedbyclient')

    async def close(self):
        self.cancel.set()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.tasks.clear()


# This fixed program returns bounded strings, rather than transferring an
# unbounded DOM into Python before clipping. Selectors are data arguments.
SNAPSHOT = """({chars, count, selectors}) => {
 const root = document.querySelector('main, article') || document.body;
 const walker = document.createTreeWalker(root || document, NodeFilter.SHOW_TEXT);
 let text = '', node, visited = 0;
 while ((node = walker.nextNode()) && text.length <= chars && visited++ < 20000) {
   const p = node.parentElement;
   if (p && p.getClientRects().length && !p.closest('script,style,noscript,template,input,textarea,select,[hidden]'))
     text += node.textContent.slice(0, chars + 1 - text.length) + ' ';
 }
 const elements = [];
 const allElements = document.querySelectorAll('a[href],button,input:not([type=password]),textarea,select');
 for (const e of allElements) {
   if (elements.length >= count) break;
   elements.push({tag:e.tagName.toLowerCase(), text:(e.innerText || e.getAttribute('aria-label') || '').slice(0,160),
     name:(e.getAttribute('name') || '').slice(0,100), type:(e.getAttribute('type') || '').slice(0,30),
     href:e.tagName === 'A' ? e.href.slice(0,4096) : null});
 }
 const fields = Object.create(null);
 let fieldPartial = false;
 for (const [key, selector] of Object.entries(selectors)) {
   const e = document.querySelector(selector);
   if (e && (e.textContent || '').length > 1000) fieldPartial = true;
   fields[key] = e ? (e.textContent || '').slice(0,1000) : null;
 }
 return {title:document.title.slice(0,300), text:text.slice(0,chars), elements, fields,
   partial:text.length > chars || visited >= 20000 || fieldPartial || allElements.length > count};
}"""


async def render(url, policy, limits=BrowserLimits(), *, selectors=None, capture=False,
                 validate=None, cancel_event=None, form_selector=None, fields=None):
    """Render one page with a fresh SDK/browser/context; return observations + PNG."""
    destination(url, policy)
    selectors = {} if selectors is None else selectors
    if form_selector is not None:
        validate_fields(form_selector,fields)
    if (not isinstance(selectors, dict) or len(selectors) > 10 or any(
            not isinstance(key, str) or not 1 <= len(key) <= 50 or not isinstance(value, str)
            or not 1 <= len(value) <= 200 for key, value in selectors.items()) or type(capture) is not bool):
        raise NetworkError('invalid_arguments', 'Supply at most ten short named CSS selectors.')
    broker = ResourceBroker(policy, limits, validate=validate, cancel_event=cancel_event)
    broker.check()
    try:
        from playwright.async_api import async_playwright  # pylint: disable=import-error
    except ImportError as error:
        raise NetworkError('dependency_missing', 'Install the optional pinned browser dependencies and managed Chromium explicitly.') from error
    # Offline mode + deny proxy protect traffic that is not routed. Numeric
    # loopback proxy addressing needs no DNS. Browser DNS, UDP and bypass paths
    # are disabled separately; this is not an OS-level hostile-code sandbox.
    clients = set()
    async def deny(_reader, writer):
        clients.add(writer)
        writer.close()
        try:
            await writer.wait_closed()
        finally:
            clients.discard(writer)
    proxy = await asyncio.start_server(deny, '127.0.0.1', 0)
    port = proxy.sockets[0].getsockname()[1]
    png = None
    try:
        async with asyncio.timeout(limits.timeout_seconds):
            main_response = await broker.response(url, follow_redirects=True)
            navigation_url = main_response.final_url
            async with async_playwright() as sdk:
                browser = await sdk.chromium.launch(headless=True, chromium_sandbox=True,
                    proxy={'server': f'http://127.0.0.1:{port}', 'bypass': '<-loopback>'},
                    args=['--disable-quic', '--dns-prefetch-disable', '--host-resolver-rules=MAP * ~NOTFOUND',
                          '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'],
                    timeout=min(15000, limits.timeout_seconds * 1000))
                try:
                    context = await browser.new_context(offline=True, service_workers='block',
                        accept_downloads=False, java_script_enabled=True,
                        viewport={'width': 1000, 'height': 700}, device_scale_factor=1)
                    try:
                        async def read_route(route):
                            nonlocal main_response
                            request = route.request
                            if request.is_navigation_request() and (
                                    main_response is None or request.url != navigation_url or
                                    request.frame != page.main_frame):
                                broker.blocked += 1
                                broker.reasons.add('navigation_denied')
                                await route.abort('blockedbyclient')
                                return
                            if request.is_navigation_request():
                                response, main_response = main_response, None
                                headers = {name.lower(): value for name, value in response.headers
                                           if name.lower() in ('content-type', 'content-security-policy', 'x-content-type-options')}
                                await route.fulfill(status=response.status, headers=headers, body=response.body)
                                return
                            await broker.route(route)
                        await context.route('**/*', read_route)
                        async def no_socket(route):
                            broker.blocked += 1
                            broker.reasons.add('websocket_denied')
                            await route.close()
                        await context.route_web_socket('**/*', no_socket)
                        page = await context.new_page()
                        page.on('dialog', lambda dialog: dialog.dismiss())
                        async def no_popup(popup):
                            if popup != page:
                                await popup.close()
                        context.on('page', no_popup)
                        await page.goto(navigation_url, wait_until='load', timeout=limits.timeout_seconds * 1000)
                        # A small, fixed settle window covers immediate client
                        # rendering without waiting indefinitely for network idle.
                        await page.wait_for_timeout(200)
                        broker.check()
                        destination(page.url, policy)
                        observation = await page.evaluate(SNAPSHOT, {'chars': limits.max_chars,
                            'count': limits.max_elements, 'selectors': selectors})
                        if form_selector is not None:
                            if not isinstance(form_selector,str) or not 1 <= len(form_selector) <= 200:
                                raise NetworkError('invalid_arguments','Supply one short native form selector.')
                            form = await page.evaluate(FORM_SNAPSHOT, form_selector)
                            if form is None:
                                raise NetworkError('action_denied','That form is ambiguous, oversized or unsupported.')
                            observation['proposal'] = proposal(form, form_selector, fields or {}, policy)
                        if capture:
                            png = await page.screenshot(full_page=False, timeout=5000)
                            if len(png) > limits.max_screenshot_bytes:
                                raise NetworkError('resource_limit', 'The browser screenshot exceeded its byte limit.')
                        broker.check()
                        observation.update(kind='browser_page', observation='rendered_text',
                            requested_url=url, url=page.url, observed_at=datetime.now(timezone.utc).isoformat(),
                            partial=observation['partial'] or bool(broker.blocked),
                            blocked_requests=broker.blocked, partial_reasons=sorted(broker.reasons),
                            requests=broker.requests, response_bytes=broker.bytes,
                            wire_bytes=broker.wire_bytes, imported_cookies=0)
                        if png is not None:
                            observation['capture'] = {'mime_type': 'image/png', 'width': 1000,
                                'height': 700, 'bytes': len(png), 'sha256': hashlib.sha256(png).hexdigest(),
                                'storage': 'tool_result_only'}
                        return observation, png
                    finally:
                        await context.close()
                finally:
                    await browser.close()
    except TimeoutError as error:
        raise NetworkError('network_timeout', 'The browser operation reached its total time limit.') from error
    finally:
        await broker.close()
        proxy.close()
        await proxy.wait_closed()
        for writer in tuple(clients):
            writer.close()
