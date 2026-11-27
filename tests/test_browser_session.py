"""Browser authority/budgets are independent of the optional rendering SDK."""

import asyncio
from dataclasses import replace
from threading import Event
import unittest
from unittest.mock import AsyncMock, patch

from utils import browser_session as browser
from utils.public_http import HTTPResult, NetworkError, NetworkPolicy


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy = NetworkPolicy(allowed_hosts=('*',))
        self.result = HTTPResult('https://public.example/', 'https://public.example/', 200,
            (('Content-Type', 'text/html'), ('Set-Cookie', 'secret'), ('Content-Encoding', 'gzip')),
            b'<p>Observed</p>', False, 100, 0)

    async def test_private_urls_and_mutations_never_reach_transport(self):
        broker = browser.ResourceBroker(self.policy, browser.BrowserLimits())
        with patch.object(browser, 'get') as fetch:
            for url, method in [('http://127.0.0.1/', 'GET'), ('file:///private', 'GET'),
                                ('https://public.example/', 'POST'), ('https://public.example/', 'PUT')]:
                with self.subTest(url=url, method=method), self.assertRaises(NetworkError):
                    await broker.response(url, method)
            fetch.assert_not_called()

    async def test_route_never_forwards_browser_auth_headers_or_encoded_response_headers(self):
        broker = browser.ResourceBroker(self.policy, browser.BrowserLimits())
        route = AsyncMock()
        route.request.url, route.request.method = 'https://public.example/', 'GET'
        with patch.object(browser, 'get', return_value=self.result) as fetch:
            await broker.route(route)
        route.fulfill.assert_awaited_once_with(status=200, headers={'content-type':'text/html'}, body=self.result.body)
        self.assertFalse(fetch.call_args.kwargs['follow_redirects'])
        self.assertNotIn('headers', fetch.call_args.kwargs)
        route.continue_.assert_not_called()
        route.fetch.assert_not_called()

    async def test_concurrent_calls_share_request_budget_and_denials_are_partial(self):
        broker = browser.ResourceBroker(self.policy, replace(browser.BrowserLimits(), max_requests=1))
        routes = [AsyncMock(), AsyncMock()]
        for route in routes:
            route.request.url, route.request.method = 'https://public.example/', 'GET'
        with patch.object(browser, 'get', return_value=self.result) as fetch:
            await asyncio.gather(*(broker.route(r) for r in routes))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(broker.blocked, 1)
        self.assertEqual(broker.reasons, {'resource_limit'})

    async def test_revocation_cancellation_partial_and_redirect_limits_fail_closed(self):
        for args, result in [({'validate': lambda: False}, self.result),
                             ({'cancel_event': Event()}, replace(self.result, partial=True))]:
            broker = browser.ResourceBroker(self.policy, browser.BrowserLimits(), **args)
            with patch.object(browser, 'get', return_value=result), self.assertRaises(NetworkError):
                await broker.response('https://public.example/')
        cancelled = Event(); cancelled.set()
        broker = browser.ResourceBroker(self.policy, browser.BrowserLimits(), cancel_event=cancelled)
        with patch.object(browser, 'get') as fetch, self.assertRaises(NetworkError):
            await broker.response('https://public.example/')
        fetch.assert_not_called()
        broker = browser.ResourceBroker(replace(self.policy, max_redirects=0), browser.BrowserLimits())
        with patch.object(browser, 'get', return_value=replace(self.result, status=302)), self.assertRaises(NetworkError):
            await broker.response('https://public.example/')

    async def test_bad_arguments_do_not_import_or_launch_sdk(self):
        with patch('builtins.__import__', wraps=__import__) as imported:
            for arguments in ({'selectors': {'a':'x'*201}}, {'selectors': []}, {'capture':'yes'}):
                with self.assertRaises(NetworkError):
                    await browser.render('https://public.example/', self.policy, **arguments)
        self.assertFalse(any(call.args[0].startswith('playwright') for call in imported.call_args_list))


if __name__ == '__main__':
    unittest.main()
