"""Bounded public HTTP(S), with checked DNS pinned to the actual connection.

No proxy environment, cookie jar, ambient credentials or auxiliary fetches are
used. This module imports no CODA runtime and grants no network authority.
"""

from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass
import http.client
import io
import ipaddress
import math
import re
import socket
import ssl
from threading import Lock, Thread
from time import monotonic
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
import zlib

import certifi
import idna


class NetworkError(RuntimeError):
    """Content-safe failure category; never include arbitrary remote error text."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _host(value):
    if not isinstance(value, str) or not value or len(value) > 253 or any(
            char.isspace() or ord(char) < 33 or char in '%/\\@' for char in value):
        raise NetworkError('invalid_url', 'Supply an ordinary absolute HTTP(S) URL.')
    value = value.rstrip('.').lower()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        value = idna.encode(value, uts46=True, std3_rules=True).decode('ascii')
    except UnicodeError as error:
        raise NetworkError('invalid_url', 'The hostname is invalid.') from error
    if len(value) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                              for part in value.split('.')):
        raise NetworkError('invalid_url', 'The hostname is invalid.')
    return value


_V4_EXCLUSIONS = tuple(ipaddress.ip_network(value) for value in ('192.0.0.0/24', '192.88.99.0/24'))
_V6_GLOBAL = ipaddress.ip_network('2000::/3')
_V6_EXCLUSIONS = tuple(ipaddress.ip_network(value) for value in
                       ('2001::/23', '2001:db8::/32', '2002::/16', '3fff::/20'))


def public_address(value):
    """Conservative classification consistent across older supported patches.

    Exclude transition/special-purpose ranges explicitly: Python patch versions
    differ, and mapped/NAT64/6to4 addresses must not conceal an internal endpoint.
    """
    try:
        address = ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return False
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if address.version == 4:
        return not any(address in network for network in _V4_EXCLUSIONS)
    return address in _V6_GLOBAL and not any(address in network for network in _V6_EXCLUSIONS)


@dataclass(frozen=True)
class NetworkPolicy:
    allowed_hosts: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = (80, 443)
    timeout_seconds: float = 10.0
    max_response_bytes: int = 524288
    max_wire_bytes: int = 1048576
    max_redirects: int = 3

    def __post_init__(self):
        if not isinstance(self.allowed_hosts, tuple) or len(self.allowed_hosts) > 64:
            raise ValueError('Supply at most 64 explicit allowed hosts.')
        for host in self.allowed_hosts:
            if host != '*':
                try:
                    _host(host)
                except NetworkError as error:
                    raise ValueError('Allowed hosts must be ordinary hostnames or literals.') from error
        if not isinstance(self.allowed_ports, tuple) or len(self.allowed_ports) > 16 or any(
                type(port) is not int or not 1 <= port <= 65535 for port in self.allowed_ports):
            raise ValueError('Supply bounded explicit allowed ports.')
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 30:
            raise ValueError('Network timeout must be positive and at most 30 seconds.')
        for value, lower, upper in ((self.max_response_bytes, 1, 2097152),
                                     (self.max_wire_bytes, 1024, 4194304), (self.max_redirects, 0, 5)):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError('Network limits exceed their supported bounds.')


@dataclass(frozen=True)
class Destination:
    url: str
    hostname: str
    port: int
    secure: bool
    resource: str
    host_header: str


def destination(url, policy):
    if not isinstance(url, str) or not url or len(url) > 4096 or any(
            char.isspace() or ord(char) < 32 or char == '\\' for char in url):
        raise NetworkError('invalid_url', 'Supply a bounded absolute HTTP(S) URL without credentials or whitespace.')
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError('Invalid URL shape.')
        hostname = _host(parsed.hostname)
        if parsed.netloc.startswith('['):
            ipaddress.IPv6Address(parsed.hostname)
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if parsed.port == 0:
            raise ValueError('Invalid port.')
    except (ValueError, UnicodeError) as error:
        raise NetworkError('invalid_url', 'Supply an absolute HTTP(S) URL without credentials.') from error
    if port not in policy.allowed_ports:
        raise NetworkError('network_denied', 'That web port is not permitted.')
    hosts = tuple(_host(host) if host != '*' else host for host in policy.allowed_hosts)
    if '*' not in hosts and hostname not in hosts:
        raise NetworkError('network_denied', 'That hostname is not permitted by the web configuration.')
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not public_address(hostname):
            raise NetworkError('network_denied', 'Only public web destinations are permitted.')
    elif '.' not in hostname or hostname.endswith(('.localhost', '.local', '.internal', '.lan', '.home', '.invalid', '.test', '.onion')):
        raise NetworkError('network_denied', 'Local and reserved hostnames are not permitted.')
    authority = '[' + hostname + ']' if ':' in hostname else hostname
    if port != (443 if parsed.scheme == 'https' else 80):
        authority += ':' + str(port)
    path = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="/%?:@!$&'()*+,;=[]-._~")
    resource = path + ('?' + query if query else '')
    canonical = urlunsplit((parsed.scheme, authority, path, query, ''))
    if len(canonical) > 8192:
        raise NetworkError('invalid_url', 'The encoded URL exceeds its length limit.')
    return Destination(canonical, hostname, port, parsed.scheme == 'https', resource, authority)


class _Guard:
    def __init__(self, timeout, cancel_event, validate):
        self.deadline = monotonic() + timeout
        self.cancel_event = cancel_event
        self.validate = validate
        self.wire_bytes = 0

    def check(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise NetworkError('cancelled', 'Web retrieval was cancelled.')
        if self.validate is not None and self.validate() is False:
            raise NetworkError('permission_denied', 'Web permissions changed during retrieval.')
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise NetworkError('network_timeout', 'Web retrieval reached its total time limit.')
        return remaining


class Resolver:
    """At most one daemon DNS worker; a timed-out lookup cannot later connect."""

    def __init__(self):
        self._lock = Lock()
        self._busy = False

    def lookup(self, hostname, port, guard):
        with self._lock:
            if self._busy:
                raise NetworkError('network_busy', 'The previous DNS lookup is still completing.')
            self._busy = True
        future = Future()
        resolve = socket.getaddrinfo
        def work():
            failure = None
            result = None
            try:
                result = resolve(hostname, port, type=socket.SOCK_STREAM)
            except Exception as error:  # Retain no resolver text in returned failures.
                failure = error
            with self._lock:
                # Clear the lease before waking the caller, so an immediate
                # redirect cannot see a finished lookup as spuriously busy.
                self._busy = False
                if failure is not None:
                    future.set_exception(failure)
                else:
                    future.set_result(result)
        worker = Thread(target=work, name='coda-web-dns', daemon=True)
        try:
            worker.start()
        except RuntimeError:
            with self._lock:
                self._busy = False
            raise
        while True:
            remaining = guard.check()
            try:
                return future.result(timeout=min(0.05, remaining))
            except FutureTimeout:
                if future.done():
                    raise NetworkError('network_timeout', 'The DNS lookup timed out.') from None
                continue
            except OSError as error:
                raise NetworkError('network_error', 'The web hostname could not be resolved.') from error


_RESOLVER = Resolver()


def _addresses(target, guard):
    guard.check()
    try:
        literal = ipaddress.ip_address(target.hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return [(socket.AF_INET if literal.version == 4 else socket.AF_INET6, str(literal))]
    answers = _RESOLVER.lookup(target.hostname, target.port, guard)
    guard.check()
    if not answers or len(answers) > 32:
        raise NetworkError('network_denied', 'DNS returned no usable bounded destination set.')
    addresses = []
    for family, _, _, _, address in answers:
        if family not in (socket.AF_INET, socket.AF_INET6) or not public_address(address[0]):
            raise NetworkError('network_denied', 'DNS returned an unsafe or mixed destination set.')
        parsed = ipaddress.ip_address(address[0])
        if (family == socket.AF_INET) != (parsed.version == 4):
            raise NetworkError('network_denied', 'DNS returned an inconsistent address family.')
        item = family, str(parsed)
        if item not in addresses:
            addresses.append(item)
    return addresses


class _Reader(io.RawIOBase):
    def __init__(self, connection, guard, limit):
        super().__init__()
        self.connection, self.guard, self.limit = connection, guard, limit

    def readable(self):
        return True

    def readinto(self, buffer):
        self.connection.settimeout(self.guard.check())
        room = self.limit - self.guard.wire_bytes
        count = self.connection.recv_into(memoryview(buffer)[:max(1, min(len(buffer), room + 1))])
        self.guard.wire_bytes += count
        if self.guard.wire_bytes > self.limit:
            raise NetworkError('resource_limit', 'The encoded HTTP response reached its byte limit.')
        return count


class _ResponseSocket:
    def __init__(self, connection, guard, limit):
        self.connection, self.guard, self.limit = connection, guard, limit

    def makefile(self, mode):
        if mode != 'rb':
            raise ValueError('Only bounded response reading is supported.')
        return io.BufferedReader(_Reader(self.connection, self.guard, self.limit), buffer_size=8192)


def _open_socket(family):
    return socket.socket(family, socket.SOCK_STREAM)


def _connect(target, addresses, guard):
    timed_out = False
    for family, address in addresses:
        guard.check()
        connection = _open_socket(family)
        try:
            connection.settimeout(min(2.0, guard.check()))
            # Numeric socket.connect performs no second hostname resolution.
            connection.connect((address, target.port) if family == socket.AF_INET else (address, target.port, 0, 0))
            if not public_address(connection.getpeername()[0]) or ipaddress.ip_address(connection.getpeername()[0]) != ipaddress.ip_address(address):
                raise NetworkError('network_denied', 'The connected peer does not match its approved DNS address.')
            if target.secure:
                context = ssl.create_default_context()
                # Requests already supplies this pinned dependency. Supplement
                # older system stores without changing either on disk or
                # disabling certificate/hostname verification.
                context.load_verify_locations(cafile=certifi.where())
                connection.settimeout(guard.check())
                connection = context.wrap_socket(connection, server_hostname=target.hostname)
                if ipaddress.ip_address(connection.getpeername()[0]) != ipaddress.ip_address(address):
                    raise NetworkError('network_denied', 'The TLS peer changed identity.')
            guard.check()
            return connection
        except NetworkError:
            connection.close()
            raise
        except ssl.SSLError as error:
            connection.close()
            raise NetworkError('tls_error', 'The web TLS connection could not be verified.') from error
        except OSError as error:
            timed_out = timed_out or isinstance(error, TimeoutError)
            connection.close()
    guard.check()
    if timed_out:
        raise NetworkError('network_timeout', 'The approved public connection timed out.')
    raise NetworkError('network_error', 'The approved public destination could not be reached.')


@dataclass(frozen=True)
class HTTPResult:
    requested_url: str
    final_url: str
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    partial: bool
    wire_bytes: int
    redirects: int


def _body(response, policy, guard):
    encoding = response.getheader('Content-Encoding', 'identity').strip().lower()
    if encoding not in ('identity', '', 'gzip'):
        raise NetworkError('unsupported_content', 'Only identity and gzip response encodings are supported.')
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == 'gzip' else None
    body = bytearray()
    while True:
        guard.check()
        raw = response.read1(min(8192, policy.max_response_bytes - len(body) + 1))
        if not raw:
            break
        if inflater is None:
            body.extend(raw)
        else:
            body.extend(inflater.decompress(raw, policy.max_response_bytes - len(body) + 1))
        if len(body) > policy.max_response_bytes:
            return bytes(body[:policy.max_response_bytes]), True
        if inflater is not None and inflater.unused_data:
            raise NetworkError('unsupported_content', 'Concatenated or trailing compressed streams are unsupported.')
    if inflater is not None and not inflater.eof:
        raise NetworkError('network_error', 'The compressed response was incomplete.')
    if response.length not in (None, 0):
        raise NetworkError('network_error', 'The HTTP response ended before its declared length.')
    guard.check()
    return bytes(body), False


def get(url, policy, *, cancel_event=None, validate=None):
    """One bounded GET transaction. Every redirect gets fresh DNS/peer checks."""
    guard = _Guard(policy.timeout_seconds, cancel_event, validate)
    target = destination(url, policy)
    requested = target.url
    for redirects in range(policy.max_redirects + 1):
        guard.check()
        connection = _connect(target, _addresses(target, guard), guard)
        response = None
        try:
            connection.settimeout(guard.check())
            request = ('GET ' + target.resource + ' HTTP/1.1\r\nHost: ' + target.host_header
                       + '\r\nUser-Agent: CODA-WebReader/1.0\r\nAccept: text/html,text/plain,text/markdown,application/json\r\n'
                         'Accept-Encoding: gzip, identity\r\nConnection: close\r\n\r\n')
            connection.sendall(request.encode('ascii'))
            response = http.client.HTTPResponse(_ResponseSocket(connection, guard, policy.max_wire_bytes))
            response.begin()
            guard.check()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location or redirects == policy.max_redirects:
                    raise NetworkError('redirect_limit', 'The page redirect chain could not be completed within its limit.')
                following = destination(urljoin(target.url, location), policy)
                if target.secure and not following.secure:
                    raise NetworkError('network_denied', 'HTTPS-to-HTTP redirects are not permitted.')
                target = following
                continue
            if not 200 <= response.status < 300:
                raise NetworkError('http_error', 'The web server returned HTTP status ' + str(response.status) + '.')
            body, partial = _body(response, policy, guard)
            guard.check()
            return HTTPResult(requested, target.url, response.status, tuple(response.getheaders()), body,
                              partial, guard.wire_bytes, redirects)
        except NetworkError:
            raise
        except (OSError, http.client.HTTPException, UnicodeError, zlib.error) as error:
            code = 'network_timeout' if isinstance(error, TimeoutError) else 'network_error'
            raise NetworkError(code, 'The bounded web response could not be completed.') from error
        finally:
            if response is not None:
                response.close()
            connection.close()
    raise NetworkError('redirect_limit', 'The page redirect limit was reached.')
