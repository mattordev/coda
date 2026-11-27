"""Opt-in disposable browser observations over CODA's public network boundary."""

import argparse
import base64
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from threading import Event
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent
from utils.browser_session import BrowserLimits, render
from utils.public_http import NetworkError, NetworkPolicy, post_form
from utils.web_requests import public_url

CONFIG_PATH = Path(__file__).with_name('browser.config.json')
server = MCPServer('coda-browser', log_level='ERROR')


def load_config():
    with CONFIG_PATH.open('rb') as source:
        encoded = source.read(65537)
    if len(encoded) > 65536:
        raise ValueError('Oversized browser configuration.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate browser configuration key.')
            result[key] = value
        return result
    raw = json.loads(encoded,object_pairs_hook=unique)
    if not isinstance(raw, dict) or set(raw) - {'enabled', 'actions_enabled', 'network', 'limits'}:
        raise ValueError('Invalid browser configuration.')
    enabled = raw.get('enabled', False)
    actions = raw.get('actions_enabled',False)
    if type(enabled) is not bool or type(actions) is not bool:
        raise ValueError('Explicit browser enablement must be a boolean.')
    network = raw.get('network', {})
    if (not isinstance(network, dict) or not isinstance(network.get('allowed_hosts', []), list)
            or not isinstance(network.get('allowed_ports', []), list)):
        raise ValueError('Invalid network configuration.')
    policy = NetworkPolicy(**{**network, 'allowed_hosts': tuple(network.get('allowed_hosts', [])),
                             'allowed_ports': tuple(network.get('allowed_ports', [80, 443]))})
    return enabled, policy, BrowserLimits(**raw.get('limits', {})), actions


def failure(code, message):
    return CallToolResult(content=[TextContent(type='text', text=message)], isError=True,
                          structuredContent={'kind': 'browser_page', 'error': {'code': code, 'message': message}})


@server.tool(structured_output=False, annotations={'readOnlyHint': True, 'openWorldHint': True})
async def inspect_page(url: str, selectors: dict[str, str] | None = None,
                       capture: bool = False) -> CallToolResult:
    """Render one public page in a disposable browser; optionally extract named CSS text fields or capture one viewport. No login, typing, clicks or scripts supplied by the caller."""
    try:
        snapshot = load_config()
    except (OSError, ValueError, TypeError):
        return failure('configuration_error', 'The browser configuration is missing or invalid.')
    enabled, policy, limits, _actions = snapshot
    if not enabled:
        return failure('browser_disabled', 'Browser tools are disabled in the operator configuration.')

    def current():
        try:
            return load_config() == snapshot
        except (OSError, ValueError, TypeError):
            return False

    try:
        page, png = await render(public_url(url), policy, limits, selectors=selectors, capture=capture, validate=current)
        if not current():
            return failure('permission_denied', 'Browser permissions changed during the operation.')
        content = [TextContent(type='text', text='Rendered page text (possibly partial):\n' + page['text'])]
        if png is not None:
            content.append(ImageContent(type='image', data=base64.b64encode(png).decode('ascii'), mimeType='image/png'))
        return CallToolResult(content=content, structuredContent=page)
    except NetworkError as error:
        return failure(error.code, str(error))
    except Exception:  # Browser/driver exception text may contain URL secrets or page data.
        return failure('browser_error', 'The optional browser could not complete this operation. Ordinary web reading remains available.')


def _bound_proposal(page, snapshot, expires=None):
    candidate = page['proposal']
    expires = int(time.time()*1000)+60000 if expires is None else expires
    candidate['expires_at_ms'] = expires
    digest = hashlib.sha256((candidate['fingerprint'] + repr(snapshot) + str(expires)).encode()).hexdigest()
    candidate['fingerprint'] = digest + '.' + str(expires)
    return candidate


async def _form(url, form_selector, fields, expected_fingerprint=None):
    attempted = False
    try:
        snapshot = load_config()
        enabled, policy, limits, actions = snapshot
        if not enabled or not actions:
            return failure('action_denied','Browser form actions are disabled in the operator configuration.')
        expires = None
        if expected_fingerprint is not None:
            try:
                digest, expiry = expected_fingerprint.split('.')
                expires = int(expiry)
                if len(digest) != 64 or len(expiry) != 13 or time.time()*1000 >= expires:
                    raise ValueError('Invalid or expired form fingerprint.')
            except (ValueError,AttributeError):
                return failure('form_changed','The form proposal is invalid or expired. Inspect it again.')
        def current():
            try:
                return load_config() == snapshot and (expires is None or time.time()*1000 < expires)
            except (OSError,ValueError,TypeError):
                return False
        page, _ = await render(url,policy,limits,form_selector=form_selector,fields=fields,validate=current)
        candidate = _bound_proposal(page,snapshot,expires)
        if not current():
            return failure('permission_denied','Browser permissions changed while inspecting the form.')
        if expected_fingerprint is None:
            return CallToolResult(content=[TextContent(type='text',text=
                'Proposed native form action; nothing submitted.\nPOST ' + candidate['destination'] +
                '\nExact URL-encoded body: ' + candidate['body'] + '\nFingerprint: ' + candidate['fingerprint'])],
                structuredContent=candidate)
        if candidate['fingerprint'] != expected_fingerprint:
            return failure('form_changed','The page, form, field values, destination or browser permissions changed. Inspect it again before confirming.')
        try:
            attempted = True
            cancel = Event()
            task = asyncio.create_task(asyncio.to_thread(post_form,candidate['destination'],candidate['body'].encode('ascii'),
                                                        policy,validate=current,cancel_event=cancel))
            try:
                response = await asyncio.shield(task)
            finally:
                cancel.set()
                await asyncio.gather(task,return_exceptions=True)
        except Exception:
            # Never retry a mutation, including after an HTTP/socket timeout.
            return failure('action_unverified','The form request did not complete cleanly. Its external effect is unverified; inspect the site before requesting it again.')
        result = {'kind':'browser_action','observation':'http_response','url':response.final_url,
                  'method':'POST','status':response.status,'fingerprint':candidate['fingerprint'],
                  'redirect_followed':False,'effect_verified':False}
        return CallToolResult(content=[TextContent(type='text',text=
            'The approved form request received HTTP status ' + str(response.status) +
            '. No redirect was followed. The website action itself has not been independently verified.')],structuredContent=result)
    except NetworkError as error:
        return failure(error.code,str(error))
    except (OSError,ValueError,TypeError):
        return failure('configuration_error','The browser configuration or form is invalid.')
    except Exception:
        if attempted:
            return failure('action_unverified','The external form effect is unverified. Inspect the website before requesting it again.')
        return failure('browser_error','The optional browser could not inspect this form. No approved form request was started.')


@server.tool(structured_output=False, annotations={'readOnlyHint':True,'openWorldHint':True})
async def prepare_action(url: str, form_selector: str, fields: dict[str,str]) -> CallToolResult:
    """Inspect one native POST form or its submit button and propose exact field values; submit nothing. Passwords, uploads and arbitrary JavaScript actions are unsupported."""
    return await _form(url,form_selector,fields)


@server.tool(structured_output=False, annotations={'readOnlyHint':False,'destructiveHint':True,'openWorldHint':True})
async def execute_action(url: str, form_selector: str, fields: dict[str,str], expected_fingerprint: str) -> CallToolResult:
    """After CODA's mandatory one-use confirmation, re-inspect and send exactly one unchanged native POST form. Use the exact prepare_action fingerprint; never retry automatically."""
    return await _form(url,form_selector,fields,expected_fingerprint)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    CONFIG_PATH = parser.parse_args().config.resolve()
    server.run(transport='stdio')
