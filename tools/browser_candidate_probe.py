"""Optional fresh-browser lifecycle/rendering probe, not a security-boundary claim."""
import argparse
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socketserver
import struct
import sys
import tempfile
from threading import Thread
import time

import psutil



async def evaluate(config):
    if config['packages_dir'] is not None:
        sys.path.insert(0, str(config['packages_dir']))
    if config['browsers_dir'] is not None:
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(config['browsers_dir'])
    os.environ['PLAYWRIGHT_SKIP_BROWSER_GC'] = '1'
    from playwright.async_api import async_playwright  # pylint: disable=import-error  # Optional evaluation dependency.
    received, blocked, sockets = [], [], []

    class HTTP(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(403)
            self.end_headers()

        def log_message(self, *_):
            pass

    class Deny(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            try:
                self.request.recv(8192)
                self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass

    class Proxy(socketserver.ThreadingTCPServer):
        daemon_threads = True

    fixture = ThreadingHTTPServer(('127.0.0.1', 0), HTTP)
    fixture.daemon_threads = True
    proxy = Proxy(('127.0.0.1', 0), Deny)
    threads = [Thread(target=server.serve_forever, daemon=True) for server in (fixture, proxy)]
    for thread in threads:
        thread.start()
    parent = psutil.Process()
    baseline = {process.pid for process in parent.children(recursive=True)}
    owned = []
    profile_paths = []
    report = {'enabled': True, 'scope': 'Controlled offline HTML, fresh-session rendering/capture/lifecycle only; not public navigation or proven browser containment.'}
    try:
        async with async_playwright() as sdk:
            browser = await sdk.chromium.launch(channel=config['channel'], headless=True, chromium_sandbox=True,
                proxy={'server': f'http://127.0.0.1:{proxy.server_address[1]}', 'bypass': '<-loopback>'},
                args=['--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'], timeout=45000)
            try:
                for process in parent.children(recursive=True):
                    if process.pid not in baseline:
                        owned.append((process.pid, process.create_time()))
                        for argument in process.cmdline():
                            if argument.startswith('--user-data-dir='):
                                path = Path(argument.split('=', 1)[1]).resolve()
                                assert path.is_relative_to(Path(tempfile.gettempdir()).resolve()), 'Browser profile is not disposable.'
                                profile_paths.append(path)
                context = await browser.new_context(offline=True, service_workers='block',
                    accept_downloads=False, java_script_enabled=True, viewport={'width': 800, 'height': 600})
                try:
                    assert await context.cookies() == [], 'Fresh context unexpectedly imported cookies.'
                    async def refuse(route):
                        blocked.append(route.request.url)
                        await route.abort('blockedbyclient')
                    async def refuse_socket(route):
                        sockets.append(route.url)
                        await route.close()
                    await context.route('**/*', refuse)
                    await context.route_web_socket('**/*', refuse_socket)
                    page = await context.new_page()
                    await page.set_content(f'''<main><h1>Isolated probe</h1><p id="result">Initial</p></main>
                        <img src="http://127.0.0.1:{fixture.server_address[1]}/private-image">
                        <script>document.querySelector('#result').textContent='Rendered value 42';
                        fetch('http://127.0.0.1:{fixture.server_address[1]}/private-fetch').catch(()=>{{}});
                        new WebSocket('ws://127.0.0.1:{fixture.server_address[1]}/private-socket');</script>''',
                        wait_until='domcontentloaded', timeout=5000)
                    text = await page.locator('main').inner_text(timeout=5000)
                    assert 'Rendered value 42' in text, text
                    await page.wait_for_timeout(200)
                    assert received == [], received
                    with tempfile.TemporaryDirectory(prefix='coda-browser-capture-') as directory:
                        capture = Path(directory) / 'capture.png'
                        png = await page.screenshot(path=str(capture), full_page=False, timeout=5000)
                        assert len(png) <= 2 * 1024 * 1024 and png.startswith(b'\x89PNG\r\n\x1a\n')
                        dimensions = struct.unpack('>II', png[16:24])
                        assert dimensions == (800, 600), dimensions
                        report.update(channel=config['channel'] or 'managed-headless-shell', version=browser.version, rendered_text=text, screenshot_bytes=len(png),
                                      screenshot_sha256=hashlib.sha256(png).hexdigest(), dimensions=dimensions,
                                      imported_cookies=0, rejected_routes=len(blocked), rejected_websockets=len(sockets),
                                      internal_fixture_requests=len(received), disposable_profiles=len(set(profile_paths)))
                finally:
                    await asyncio.wait_for(context.close(), 5)
            finally:
                await asyncio.wait_for(browser.close(), 5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            alive = []
            for pid, created in owned:
                try:
                    if psutil.Process(pid).create_time() == created:
                        alive.append(pid)
                except psutil.NoSuchProcess:
                    pass
            if not alive:
                break
            await asyncio.sleep(0.05)
        assert not alive, f'Owned browser resources remain: {alive}'
        report.update(owned_processes_closed=len(owned), owned_processes_remaining=0,
                      disposable_profiles_removed=all(not path.exists() for path in profile_paths))
        assert report['disposable_profiles_removed']
        return report
    finally:
        for server in (fixture, proxy):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enabled', action='store_true', help='Explicitly enable the controlled offline browser probe.')
    parser.add_argument('--channel', choices=('managed-headless-shell', 'msedge'), default='managed-headless-shell')
    parser.add_argument('--packages-dir', type=Path, help='Optional isolated Python dependency directory.')
    parser.add_argument('--browsers-dir', type=Path, help='Optional isolated Playwright browser binary directory.')
    parser.add_argument('--output', type=Path, help='Optional new JSON evidence file; existing files are not overwritten.')
    args = parser.parse_args(argv)
    if not args.enabled:
        result = {'enabled': False, 'owned_child_processes': len(psutil.Process().children()),
                  'sdk_imported': any(name.startswith('playwright') for name in sys.modules)}
    else:
        for path in (args.packages_dir, args.browsers_dir):
            if path is not None and not path.is_dir():
                parser.error('Isolated dependency/browser directories must already exist; this tool installs nothing.')
        config = {'channel': None if args.channel == 'managed-headless-shell' else args.channel,
                  'packages_dir': args.packages_dir, 'browsers_dir': args.browsers_dir}
        result = asyncio.run(evaluate(config))
    if args.output:
        with args.output.open('x', encoding='utf-8') as file:
            json.dump(result, file, indent=2)
            file.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
