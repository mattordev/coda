"""Opt-in real Chromium + owned HTTP fixture. No production private-IP override."""

import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

import psutil

from utils import browser_session as browser, public_http as web
from ai.intents import IntentRequest
from ai.mcp.config import discover_definitions
from ai.mcp.runtime import LocalMCPManager


@unittest.skipUnless(os.environ.get('CODA_RUN_BROWSER_SMOKE') == '1', 'Explicit optional Chromium integration opt-in required.')
class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_render_capture_public_broker_denials_and_cleanup(self):
        received = []
        mutations=[]
        form_version=['v1']
        ambient=[]
        udp = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1',0));udp.settimeout(.1)
        udp_port=udp.getsockname()[1]
        datagrams=[];udp_stop=Event()
        def receive_udp():
            while not udp_stop.is_set():
                try:
                    datagrams.append(udp.recvfrom(8192)[0])
                except socket.timeout:
                    continue
                except OSError:
                    break
        udp_thread=Thread(target=receive_udp,daemon=True);udp_thread.start()
        def close_udp():
            udp_stop.set();udp.close();udp_thread.join(2)
        self.addCleanup(close_udp)
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                ambient.extend(name for name in ('Cookie','Authorization') if self.headers.get(name))
                received.append(self.path)
                if self.path == '/start':
                    self.send_response(302); self.send_header('Location', '/page'); self.end_headers(); return
                if self.path == '/redirect-script':
                    self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{self.server.server_port}/redirect-private');self.end_headers();return
                body = (f'''<html><head><title>Controlled browser fixture</title></head><body>
                  <main><h1 id="result">Before JavaScript</h1><p>A literal source observation.</p></main>
                  <form id="test-form" method="post" action="/submit">
                    <input name="message" value="before"><input type="hidden" name="nonce" value="{form_version[0]}">
                    <button type="submit">Submit</button>
                  </form>
                  <script src="/script.js"></script>
                  <script src="/redirect-script"></script>
                  <img src="http://127.0.0.1:{self.server.server_port}/private-image">
                  <iframe src="http://127.0.0.1:{self.server.server_port}/private-frame"></iframe>
                  <script>
                    fetch('/mutation', {{method:'POST',body:'unapproved'}}).catch(()=>{{}});
                    navigator.sendBeacon('/beacon','unapproved');
                    fetch('http://169.254.169.254/latest/meta-data/').catch(()=>{{}});
                    new WebSocket('ws://127.0.0.1:{self.server.server_port}/private-socket');
                    if (navigator.serviceWorker) navigator.serviceWorker.register('/worker.js').catch(()=>{{}});
                    window.open('http://127.0.0.1:{self.server.server_port}/private-popup');
                    const peer = new RTCPeerConnection({{iceServers:[{{urls:'stun:127.0.0.1:{udp_port}'}}]}});
                    peer.createDataChannel('probe');
                    peer.createOffer().then(o=>peer.setLocalDescription(o)).catch(()=>{{}});
                  </script></body></html>''').encode()
                mime = 'text/html'
                if self.path == '/script.js':
                    body = b'document.querySelector("#result").textContent="Rendered value 42";'
                    mime = 'application/javascript'
                self.send_response(200); self.send_header('Content-Type', mime)
                self.send_header('Set-Cookie', 'must_not_forward=secret')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                ambient.extend(name for name in ('Cookie','Authorization') if self.headers.get(name))
                received.append('POST ' + self.path)
                mutations.append(self.rfile.read(int(self.headers.get('Content-Length','0'))))
                self.send_response(204); self.end_headers()
            def log_message(self, *_):
                pass
        fixture = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        fixture.daemon_threads = True
        thread = Thread(target=fixture.serve_forever, daemon=True); thread.start()
        self.addCleanup(fixture.server_close); self.addCleanup(fixture.shutdown); self.addCleanup(lambda: thread.join(2))
        port = fixture.server_port
        original_socket = socket.socket
        class MappedSocket:
            def __init__(self, _family):
                self.inner = original_socket(socket.AF_INET, socket.SOCK_STREAM)
            def __getattr__(self, name):
                return getattr(self.inner, name)
            def connect(self, address):
                if address != ('93.184.216.34', port):
                    raise OSError('Unexpected fixture address.')
                self.inner.connect(('127.0.0.1', port))
            def getpeername(self):
                return '93.184.216.34', port
        def resolve(_self, host, requested_port, guard):
            guard.check()
            if host != 'public.example' or requested_port != port:
                raise OSError('Unexpected fixture DNS.')
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]
        parent = psutil.Process()
        before = {(p.pid, p.create_time()) for p in parent.children(recursive=True)}
        seen, profiles = [], []
        # Observe only this test's owned descendants while actual rendering runs.
        async def observe():
            while True:
                for proc in parent.children(recursive=True):
                    try:
                        identity = (proc.pid, proc.create_time())
                        if identity not in before and identity not in seen:
                            seen.append(identity)
                            for arg in proc.cmdline():
                                if arg.startswith('--user-data-dir='):
                                    profiles.append(Path(arg.split('=',1)[1]))
                    except psutil.NoSuchProcess:
                        pass
                await asyncio.sleep(.05)
        observer = asyncio.create_task(observe())
        try:
            with patch.object(web.Resolver, 'lookup', resolve), patch.object(web, '_open_socket', MappedSocket):
                result, png = await browser.render(f'http://public.example:{port}/start',
                    web.NetworkPolicy(allowed_hosts=('public.example',), allowed_ports=(port,)),
                    browser.BrowserLimits(), selectors={'heading':'#result'}, capture=True)
            self.assertIn('Rendered value 42', result['text'])
            self.assertEqual(result['fields'], {'heading':'Rendered value 42'})
            self.assertEqual(result['url'], f'http://public.example:{port}/page')
            self.assertEqual(result['imported_cookies'], 0)
            self.assertTrue(result['partial'])
            self.assertIn('action_denied', result['partial_reasons'])
            self.assertIn('websocket_denied', result['partial_reasons'])
            self.assertEqual(received, ['/start','/page','/script.js','/redirect-script'])
            self.assertFalse(datagrams,'Browser UDP must not reach the owned internal STUN listener.')
            self.assertEqual(struct.unpack('>II',png[16:24]),(1000,700))
            self.assertLessEqual(len(png),524288)
            with tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary)
                config=directory/'browser-config.json'
                config.write_text(json.dumps({'enabled':True,'actions_enabled':True,
                    'network':{'allowed_hosts':['public.example'],'allowed_ports':[port]}}))
                definitions=directory/'definitions';definitions.mkdir()
                (definitions/'browser.json').write_text(json.dumps({'id':'browser','bundled_server':'browser',
                    'enabled':True,'trusted':True,'allowed_tools':['inspect_page','prepare_action','execute_action'],
                    'settings':{'config_file':str(config)},'timeout_seconds':65}))
                definition=discover_definitions(definitions).servers[0]
                self.assertIsNone(definition.bundle_error)
                fixture_script=Path(__file__).with_name('mcp_browser_fixture.py')
                refs=tuple((key,key) for key in ('PYTHONPATH','PLAYWRIGHT_BROWSERS_PATH','CHROME_DEVEL_SANDBOX') if key in os.environ)
                for mode in ('operation','persistent'):
                    manager=LocalMCPManager([replace(definition,session_mode=mode,
                        args=(str(fixture_script),'--config',str(config),'--port',str(port)),env_refs=refs)])
                    try:
                        discovery=await asyncio.to_thread(manager.discover,'browser')
                        self.assertTrue(discovery.success,discovery.error)
                        names={intent.name.rsplit('.',1)[-1] for intent in discovery.data}
                        self.assertEqual(names,{'inspect_page','prepare_action','execute_action'})
                        async def invoke(name,arguments,confirmed=False):
                            executor=manager.executor('mcp.browser.'+name)
                            request=IntentRequest(executor._intent,'Explicit controlled fixture request',1,arguments=arguments)
                            return await asyncio.to_thread(executor.execute,request,confirmed=confirmed)
                        args={'url':f'http://public.example:{port}/page','form_selector':'#test-form','fields':{'message':'user approved'}}
                        prepared=await invoke('prepare_action',args)
                        self.assertTrue(prepared.success,prepared.error)
                        fingerprint=prepared.data['structuredContent']['fingerprint']
                        action={**args,'expected_fingerprint':fingerprint}
                        count=len(mutations)
                        self.assertEqual((await invoke('execute_action',action)).error.code,'confirmation_required')
                        self.assertEqual(len(mutations),count)
                        acted=await invoke('execute_action',action,True)
                        self.assertTrue(acted.success,acted.error)
                        self.assertEqual(acted.data['structuredContent']['status'],204)
                        self.assertEqual(mutations[count:],[b'message=user+approved&nonce='+form_version[0].encode()])
                        self.assertEqual((await invoke('execute_action',action,True)).error.code,'approval_missing')
                        prepared=await invoke('prepare_action',args)
                        self.assertTrue(prepared.success,prepared.error)
                        action={**args,'expected_fingerprint':prepared.data['structuredContent']['fingerprint']}
                        form_version[0]+='x'
                        changed=await invoke('execute_action',action,True)
                        self.assertFalse(changed.success)
                        self.assertEqual(changed.data['structuredContent']['error']['code'],'form_changed')
                        self.assertEqual(len(mutations),count+1)
                    finally:
                        await asyncio.to_thread(manager.close)
                pids=[int(value) for value in config.with_suffix('.pids').read_text().split()]
                self.assertTrue(pids)
                self.assertTrue(all(not psutil.pid_exists(pid) for pid in pids))
                self.assertFalse(datagrams)
                self.assertFalse(ambient,'Browser cookies/authorization must not enter brokered requests.')
        finally:
            observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            alive = [pid for pid, created in seen if psutil.pid_exists(pid) and psutil.Process(pid).create_time() == created]
            if not alive:
                break
            await asyncio.sleep(.05)
        self.assertTrue(seen, 'The real optional browser must have launched.')
        self.assertFalse(alive)
        self.assertTrue(profiles, 'A disposable browser profile must have been observed.')
        self.assertTrue(all(not path.exists() for path in profiles))


if __name__ == '__main__':
    unittest.main()
