"""Public-only DNS/peer boundaries and actual bounded HTTP protocol fixtures."""

from dataclasses import replace
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
import socket
import ssl
from threading import Event, Thread
import time
import unittest
from unittest.mock import Mock, patch

from utils import public_http as web

PUBLIC = '93.184.216.34'


def answer(hostname, port, **_):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC, port))]


class MemorySocket:
    def __init__(self, response=b'HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello', peer=PUBLIC):
        self.input = io.BytesIO(response)
        self.peer = peer
        self.connected = None
        self.sent = b''
        self.closed = False
        self.timeout = None

    def connect(self, address):
        self.connected = address

    def settimeout(self, timeout):
        self.timeout = timeout

    def getpeername(self):
        return self.peer, 80

    def sendall(self, data):
        self.sent += data

    def recv_into(self, target):
        return self.input.readinto(target)

    def close(self):
        self.closed = True


class PublicHTTPTests(unittest.TestCase):
    def test_form_post_sends_exact_body_without_ambient_headers_and_never_follows_redirect(self):
        connection = MemorySocket(b'HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/private\r\nContent-Length: 0\r\n\r\n')
        with patch.object(web.socket,'getaddrinfo',side_effect=answer) as dns, patch.object(web.socket,'socket',return_value=connection):
            result=web.post_form('http://public.example/submit',b'message=approved',self.policy)
        self.assertEqual(result.status,302)
        self.assertEqual(result.redirects,0)
        self.assertEqual(dns.call_count,1)
        self.assertTrue(connection.sent.startswith(b'POST /submit HTTP/1.1\r\n'))
        self.assertTrue(connection.sent.endswith(b'\r\n\r\nmessage=approved'))
        self.assertIn(b'Content-Length: 16\r\n',connection.sent)
        self.assertNotIn(b'Cookie:',connection.sent)
        self.assertNotIn(b'Authorization:',connection.sent)
        self.assertTrue(connection.closed)

    def test_form_post_does_not_retry_a_failed_send(self):
        connection=MemorySocket()
        connection.sendall=Mock(side_effect=OSError('private transport diagnostic'))
        with patch.object(web.socket,'getaddrinfo',side_effect=answer), patch.object(web.socket,'socket',return_value=connection), self.assertRaises(web.NetworkError):
            web.post_form('http://public.example/submit',b'message=approved',self.policy)
        self.assertEqual(connection.sendall.call_count,1)
        self.assertTrue(connection.closed)

    def test_browser_redirect_handoff_preserves_origin_and_rejects_unsafe_destinations(self):
        raw = b'HTTP/1.1 302 Found\r\nLocation: https://other.example/final\r\nContent-Length: 0\r\n\r\n'
        result, connection, dns = self.fetch(raw, follow_redirects=False)
        self.assertEqual(result.status, 302)
        self.assertEqual(result.final_url, 'http://public.example/page')
        self.assertEqual(result.headers, (('Location', 'https://other.example/final'),))
        self.assertEqual(dns.call_count, 1)
        self.assertTrue(connection.closed)
        for location in ('http://127.0.0.1/private', 'http://public.example/down'):
            raw = ('HTTP/1.1 302 Found\r\nLocation: ' + location + '\r\nContent-Length: 0\r\n\r\n').encode()
            tls = Mock()
            tls.wrap_socket.side_effect = lambda connection, **_: connection
            with patch.object(web.ssl, 'create_default_context', return_value=tls), self.assertRaises(web.NetworkError):
                self.fetch(raw, url='https://public.example/page', follow_redirects=False)

    def setUp(self):
        self.policy = web.NetworkPolicy(allowed_hosts=('*',))
        resolver = patch.object(web, '_RESOLVER', web.Resolver())
        resolver.start()
        self.addCleanup(resolver.stop)

    def assert_network_error(self, code, function, *args, **kwargs):
        with self.assertRaises(web.NetworkError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def fetch(self, response=None, *, policy=None, peer=PUBLIC, url='http://public.example/page', **kwargs):
        connection = MemorySocket(peer=peer) if response is None else MemorySocket(response, peer)
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer) as dns, \
             patch.object(web.socket, 'socket', return_value=connection):
            result = web.get(url, policy or self.policy, **kwargs)
        return result, connection, dns

    def test_default_policy_grants_no_network_and_explicit_host_lists_are_exact(self):
        self.assert_network_error('network_denied', web.destination, 'https://example.com', web.NetworkPolicy())
        policy = replace(self.policy, allowed_hosts=('EXAMPLE.COM.',))
        self.assertEqual(web.destination('https://example.com/', policy).hostname, 'example.com')
        self.assert_network_error('network_denied', web.destination, 'https://sub.example.com', policy)
        self.assert_network_error('network_denied', web.destination, 'https://example.com:8443', policy)

    def test_private_reserved_transition_and_mapped_addresses_are_never_public(self):
        for address in ('0.0.0.0', '10.0.0.1', '100.64.0.1', '127.0.0.1', '169.254.169.254',
                        '172.16.0.1', '192.168.1.1', '192.0.0.8', '192.0.2.1', '192.88.99.1',
                        '198.18.0.1', '198.51.100.1', '203.0.113.1', '224.0.0.1', '255.255.255.255',
                        '::', '::1', '::ffff:127.0.0.1', '64:ff9b::a9fe:a9fe', '64:ff9b:1::1',
                        '100::1', '2001::1', '2001:db8::1', '2002:7f00:1::', '3fff::1',
                        'fc00::1', 'fe80::1', 'fec0::1', 'ff02::1'):
            with self.subTest(address=address):
                self.assertFalse(web.public_address(address))
        for address in (PUBLIC, '8.8.8.8', '2606:4700:4700::1111', '2001:4860:4860::8888'):
            self.assertTrue(web.public_address(address), address)

    def test_malformed_credentials_controls_and_reserved_hostnames_fail_before_dns(self):
        with patch.object(web.socket, 'getaddrinfo') as dns:
            for url in ('file:///etc/passwd', 'ftp://example.com', '//example.com', 'https://user:pass@example.com',
                        'https://example.com\\@127.0.0.1', 'https://%31%32%37.0.0.1',
                        'https://[fe80::1%25eth0]', 'http://example.com:0', 'https://example.com:99999',
                        'https://example.com/\r\nInjected: yes', 'http://localhost/', 'http://foo.local/',
                        'http://foo.internal/', 'http://foo.localhost/', 'http://foo.invalid/', 'http://[v1.fe]/'):
                with self.subTest(url=url), self.assertRaises(web.NetworkError):
                    web.get(url, self.policy)
            dns.assert_not_called()

    def test_invalid_policy_types_and_nonfinite_or_out_of_range_limits_are_rejected(self):
        for arguments in ({'allowed_hosts': ['*']}, {'allowed_hosts': (None,)}, {'allowed_ports': (True,)},
                          {'max_response_bytes': True}, {'max_wire_bytes': 1023}, {'max_redirects': 6},
                          {'timeout_seconds': float('nan')}, {'timeout_seconds': float('inf')}, {'timeout_seconds': True}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                web.NetworkPolicy(**arguments)

    def test_public_ipv6_literals_use_numeric_family_without_dns(self):
        address = '2606:4700:4700::1111'
        connection = MemorySocket(peer=address)
        with patch.object(web.socket, 'getaddrinfo') as dns, patch.object(web.socket, 'socket', return_value=connection):
            result = web.get('http://[' + address + ']/page#fragment', self.policy)
        dns.assert_not_called()
        self.assertEqual(connection.connected, (address, 80, 0, 0))
        self.assertIn(('Host: [' + address + ']\r\n').encode(), connection.sent)
        self.assertNotIn(b'#fragment', connection.sent)
        self.assertEqual(result.body, b'hello')

    def test_unicode_hostname_and_resource_are_normalised_without_changing_authority(self):
        result, connection, dns = self.fetch(url='http://b\u00fccher.example/caf\u00e9?q=\u00e9')
        self.assertTrue(result.final_url.startswith('http://xn--bcher-kva.example/'))
        self.assertIn(b'GET /caf%C3%A9?q=%C3%A9 HTTP/1.1\r\n', connection.sent)
        self.assertIn(b'Host: xn--bcher-kva.example\r\n', connection.sent)
        self.assertEqual(dns.call_args.args[0], 'xn--bcher-kva.example')
        policy = replace(self.policy, allowed_hosts=('xn--fa-hia.example',))
        self.assertEqual(web.destination('https://fa\u00df.example/', policy).hostname, 'xn--fa-hia.example')

    def test_encoded_ipv4_variants_resolve_but_are_denied_before_connecting(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]
        with patch.object(web.socket, 'getaddrinfo', return_value=private), patch.object(web.socket, 'socket') as dial:
            for host in ('2130706433', '0x7f000001', '0177.0.0.1', '127.1'):
                self.assert_network_error('network_denied', web.get, 'http://' + host, self.policy)
            dial.assert_not_called()

    def test_mixed_dns_answers_and_address_family_mismatches_fail_closed(self):
        public = answer('public.example', 80)
        for answers in (public + [(socket.AF_INET, 1, 6, '', ('10.0.0.1', 80))],
                        [(socket.AF_INET, 1, 6, '', ('2606:4700:4700::1111', 80))], public * 33):
            with patch.object(web.socket, 'getaddrinfo', return_value=answers), patch.object(web.socket, 'socket') as dial:
                self.assert_network_error('network_denied', web.get, 'http://public.example/', self.policy)
                dial.assert_not_called()

    def test_checked_numeric_address_is_pinned_without_second_dns_and_no_ambient_headers(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1'}):
            result, connection, dns = self.fetch()
        self.assertEqual(result.body, b'hello')
        self.assertEqual(connection.connected, (PUBLIC, 80))
        self.assertEqual(dns.call_count, 1)
        self.assertIn(b'Host: public.example\r\n', connection.sent)
        self.assertNotIn(b'Authorization:', connection.sent)
        self.assertNotIn(b'Cookie:', connection.sent)
        self.assertTrue(connection.closed)

    def test_unapproved_peer_closes_before_any_http_request(self):
        connection = MemorySocket(peer='127.0.0.1')
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer), patch.object(web.socket, 'socket', return_value=connection):
            self.assert_network_error('network_denied', web.get, 'http://public.example/', self.policy)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.sent, b'')

    def test_tls_uses_verified_default_context_and_original_sni(self):
        connection = MemorySocket()
        context = Mock()
        context.wrap_socket.return_value = connection
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer), \
             patch.object(web.socket, 'socket', return_value=connection), \
             patch.object(web.ssl, 'create_default_context', return_value=context) as create:
            result = web.get('https://public.example/page', self.policy)
        self.assertEqual(result.body, b'hello')
        create.assert_called_once_with()
        context.load_verify_locations.assert_called_once_with(cafile=web.certifi.where())
        context.wrap_socket.assert_called_once_with(connection, server_hostname='public.example')
        self.assertTrue(connection.closed)

    def test_bad_tls_certificate_never_sends_http_or_falls_back_to_unverified_tls(self):
        connection = MemorySocket()
        context = Mock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError('untrusted certificate body')
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer), \
             patch.object(web.socket, 'socket', return_value=connection), \
             patch.object(web.ssl, 'create_default_context', return_value=context):
            self.assert_network_error('tls_error', web.get, 'https://public.example/', self.policy)
        self.assertEqual(connection.sent, b'')
        self.assertTrue(connection.closed)

    def test_redirect_private_destination_and_mixed_rebinding_are_rejected(self):
        for location, following in (('http://127.0.0.1/', None), ('http://changed.example/',
                [(socket.AF_INET, 1, 6, '', ('169.254.169.254', 80))])):
            connection = MemorySocket(('HTTP/1.1 302 Found\r\nLocation: ' + location + '\r\n\r\n').encode())
            resolutions = [answer('public.example', 80)] + ([following] if following else [])
            with patch.object(web.socket, 'getaddrinfo', side_effect=resolutions) as dns, \
                 patch.object(web.socket, 'socket', return_value=connection) as dial:
                self.assert_network_error('network_denied', web.get, 'http://public.example/', self.policy)
                self.assertEqual(dial.call_count, 1)
                self.assertEqual(dns.call_count, len(resolutions))
            self.assertTrue(connection.closed)

    def test_redirect_final_url_is_recorded_and_redirect_body_is_not_read_whole(self):
        first = MemorySocket(b'HTTP/1.1 302 Found\r\nLocation: /final\r\nContent-Length: 999999999\r\n\r\n')
        second = MemorySocket()
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer) as dns, \
             patch.object(web.socket, 'socket', side_effect=[first, second]):
            result = web.get('http://public.example/original', self.policy)
        self.assertEqual(result.requested_url, 'http://public.example/original')
        self.assertEqual(result.final_url, 'http://public.example/final')
        self.assertEqual(result.redirects, 1)
        self.assertEqual(dns.call_count, 2)
        self.assertTrue(first.closed and second.closed)

    def test_same_hostname_redirect_rebind_is_checked_again_before_connection(self):
        connection = MemorySocket(b'HTTP/1.1 302 Found\r\nLocation: /next\r\n\r\n')
        responses = [answer('public.example', 80), [(socket.AF_INET, 1, 6, '', ('127.0.0.1', 80))]]
        with patch.object(web.socket, 'getaddrinfo', side_effect=responses), patch.object(web.socket, 'socket', return_value=connection) as dial:
            self.assert_network_error('network_denied', web.get, 'http://public.example/', self.policy)
            self.assertEqual(dial.call_count, 1)
        self.assertTrue(connection.closed)

    def test_redirect_loops_and_tls_downgrades_are_bounded(self):
        for url, response, code in (('http://public.example/', b'HTTP/1.1 302 Found\r\nLocation: /again\r\n\r\n', 'redirect_limit'),
                ('https://public.example/', b'HTTP/1.1 302 Found\r\nLocation: http://public.example/plain\r\n\r\n', 'network_denied')):
            connections = []
            def create(*_):
                current = MemorySocket(response)
                connections.append(current)
                return current
            context = Mock()
            context.wrap_socket.side_effect = lambda connection, **_: connection
            with patch.object(web.socket, 'getaddrinfo', side_effect=answer), patch.object(web.socket, 'socket', side_effect=create), \
                 patch.object(web.ssl, 'create_default_context', return_value=context):
                self.assert_network_error(code, web.get, url, self.policy)
            self.assertLessEqual(len(connections), self.policy.max_redirects + 1)
            self.assertTrue(all(connection.closed for connection in connections))

    def test_decoded_and_encoded_budgets_and_gzip_bombs_are_distinct(self):
        policy = replace(self.policy, max_response_bytes=4)
        result, _, _ = self.fetch(policy=policy)
        self.assertEqual(result.body, b'hell')
        self.assertTrue(result.partial)
        compressed = gzip.compress(b'x' * 1000000)
        packet = b'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: ' + str(len(compressed)).encode() + b'\r\n\r\n' + compressed
        result, _, _ = self.fetch(packet, policy=policy)
        self.assertEqual(result.body, b'xxxx')
        self.assertTrue(result.partial)
        large_headers = b'HTTP/1.1 200 OK\r\nX-Padding: ' + b'x' * 2000 + b'\r\nContent-Length: 0\r\n\r\n'
        with self.assertRaises(web.NetworkError) as caught:
            self.fetch(large_headers, policy=replace(self.policy, max_wire_bytes=1024))
        self.assertEqual(caught.exception.code, 'resource_limit')

    def test_truncated_http_and_gzip_streams_and_unknown_encodings_do_not_claim_complete_text(self):
        for packet in (b'HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\nshort',
                       b'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 3\r\n\r\nbad'):
            with self.assertRaises(web.NetworkError):
                self.fetch(packet)
        with self.assertRaises(web.NetworkError) as caught:
            self.fetch(b'HTTP/1.1 200 OK\r\nContent-Encoding: br\r\nContent-Length: 0\r\n\r\n')
        self.assertEqual(caught.exception.code, 'unsupported_content')

    def test_complete_gzip_and_chunked_bodies_and_incomplete_chunks_are_distinct(self):
        content = b'complete source text'
        compressed = gzip.compress(content)
        packet = b'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: ' + str(len(compressed)).encode() + b'\r\n\r\n' + compressed
        result, _, _ = self.fetch(packet)
        self.assertEqual(result.body, content)
        self.assertFalse(result.partial)
        result, _, _ = self.fetch(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n')
        self.assertEqual(result.body, b'hello')
        self.assertFalse(result.partial)
        with self.assertRaises(web.NetworkError):
            self.fetch(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhe')

    def test_http_error_does_not_quote_or_read_a_remote_error_body_whole(self):
        connection = MemorySocket(b'HTTP/1.1 404 Not Found\r\nContent-Length: 100000\r\n\r\n' + b'private remote prose' * 10000)
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer), patch.object(web.socket, 'socket', return_value=connection):
            with self.assertRaises(web.NetworkError) as caught:
                web.get('http://public.example/', self.policy)
        self.assertEqual(caught.exception.code, 'http_error')
        self.assertNotIn('private remote prose', str(caught.exception))
        self.assertLessEqual(connection.input.tell(), 8192)
        self.assertTrue(connection.closed)

    def test_cancellation_and_permission_change_discard_observations_and_close(self):
        cancelled = Event()
        cancelled.set()
        with patch.object(web.socket, 'socket') as dial:
            self.assert_network_error('cancelled', web.get, 'http://public.example/', self.policy, cancel_event=cancelled)
            dial.assert_not_called()
        current = MemorySocket()
        def revoke(data):
            current.sent += data
            cancelled.set()
        current.sendall = revoke
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer), patch.object(web.socket, 'socket', return_value=current):
            cancelled.clear()
            self.assert_network_error('permission_denied', web.get, 'http://public.example/', self.policy, validate=lambda: not cancelled.is_set())
        self.assertTrue(current.closed)

    def test_timed_out_dns_cannot_connect_later_or_accumulate_workers(self):
        entered, release = Event(), Event()
        resolver = web.Resolver()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(3)
            return answer(*args, **kwargs)
        with patch.object(web, '_RESOLVER', resolver), patch.object(web.socket, 'getaddrinfo', side_effect=blocked), \
             patch.object(web.socket, 'socket') as dial:
            try:
                self.assert_network_error('network_timeout', web.get, 'http://public.example/', replace(self.policy, timeout_seconds=0.1))
                self.assertTrue(entered.is_set())
                self.assert_network_error('network_busy', web.get, 'http://public.example/', self.policy)
                dial.assert_not_called()
            finally:
                release.set()
                deadline = time.monotonic() + 2
                while resolver._busy and time.monotonic() < deadline:
                    time.sleep(0.01)
            self.assertFalse(resolver._busy)
            dial.assert_not_called()

    def test_resolver_reported_timeout_returns_without_spinning_until_total_deadline(self):
        start = time.monotonic()
        with patch.object(web.socket, 'getaddrinfo', side_effect=TimeoutError('resolver timeout')):
            self.assert_network_error('network_timeout', web.get, 'http://public.example/',
                                      replace(self.policy, timeout_seconds=5))
        self.assertLess(time.monotonic() - start, 2)


class ControlledHTTPTests(unittest.TestCase):
    """Real loopback fixtures behind an explicitly injected public-peer dialer.

    The production policy has no private-IP override. Only the test socket maps
    a checked public address to our owned fixture; native public HTTPS is tested
    separately in live validation, not claimed by this controlled transport test.
    """

    def setUp(self):
        self.requests = []
        requests = self.requests
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                if self.path == '/drip':
                    self.send_response(200)
                    self.send_header('Content-Length', '100')
                    self.end_headers()
                    try:
                        for _ in range(100):
                            self.wfile.write(b'x')
                            self.wfile.flush()
                            time.sleep(0.03)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass
                else:
                    content = b'<html><title>Fixture</title><p>Untrusted text</p><img src="http://127.0.0.1/private"></html>'
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html')
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
            def log_message(self, *_):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)
        real_socket = socket.socket
        endpoint = self.server.server_address
        self.sockets = []
        owned_sockets = self.sockets
        class MappedSocket:
            def __init__(self, *_):
                self.inner = real_socket(socket.AF_INET, socket.SOCK_STREAM)
                self.requested = None
                self.closed = False
                owned_sockets.append(self)
            def __getattr__(self, name):
                return getattr(self.inner, name)
            def connect(self, address):
                self.requested = address
                self.inner.connect(endpoint)
            def getpeername(self):
                return PUBLIC, endpoint[1]
            def close(self):
                self.closed = True
                self.inner.close()
        self.mapping = MappedSocket
        self.policy = web.NetworkPolicy(allowed_hosts=('public.example',), timeout_seconds=2.0)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def test_real_http_bytes_and_metadata_are_read_without_html_auxiliary_requests(self):
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer), patch.object(web, '_open_socket', side_effect=self.mapping):
            result = web.get('http://public.example/article', self.policy)
        self.assertIn(b'Untrusted text', result.body)
        self.assertIn(('Content-Type', 'text/html'), result.headers)
        self.assertFalse(result.partial)
        self.assertEqual(self.requests, ['/article'])
        self.assertEqual(self.sockets[0].requested, (PUBLIC, 80))
        self.assertTrue(self.sockets[0].closed)

    def test_slow_drip_cannot_extend_the_total_deadline_by_resetting_inactivity(self):
        start = time.monotonic()
        with patch.object(web.socket, 'getaddrinfo', side_effect=answer), patch.object(web, '_open_socket', side_effect=self.mapping):
            with self.assertRaises(web.NetworkError) as caught:
                web.get('http://public.example/drip', replace(self.policy, timeout_seconds=0.4))
        self.assertEqual(caught.exception.code, 'network_timeout')
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(self.requests, ['/drip'])
        self.assertTrue(self.sockets[0].closed)

    def test_cancellation_during_real_body_receipt_closes_the_owned_connection(self):
        cancelled = Event()
        def cancel_after_request():
            deadline = time.monotonic() + 2
            while not self.requests and time.monotonic() < deadline:
                time.sleep(0.01)
            cancelled.set()
        worker = Thread(target=cancel_after_request, daemon=True)
        worker.start()
        try:
            with patch.object(web.socket, 'getaddrinfo', side_effect=answer), patch.object(web, '_open_socket', side_effect=self.mapping):
                with self.assertRaises(web.NetworkError) as caught:
                    web.get('http://public.example/drip', self.policy, cancel_event=cancelled)
            self.assertEqual(caught.exception.code, 'cancelled')
            self.assertEqual(self.requests, ['/drip'])
            self.assertTrue(self.sockets[0].closed)
        finally:
            cancelled.set()
            worker.join(3)


if __name__ == '__main__':
    unittest.main()
