"""Host provenance for one-use, expiring browser form approvals."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from time import monotonic

from ai.mcp.bundled import INSTALL_ROOT


def configuration_token(definition):
    if getattr(definition, 'bundled_server', None) != 'browser':
        return None
    try:
        settings = json.loads(definition.settings_json)
        path = Path(settings.get('config_file', INSTALL_ROOT/'integrations/coda-browser/browser.config.json'))
        if not path.is_absolute():
            return None
        raw = path.read_bytes()
        config = json.loads(raw)
        if len(raw) > 65536 or config.get('enabled') is not True or config.get('actions_enabled') is not True:
            return None
        identity = repr(definition).encode() + raw + json.dumps(definition.environment(),sort_keys=True).encode()
        if definition.definition_file:
            identity += Path(definition.definition_file).read_bytes()
        return hashlib.sha256(identity).hexdigest()
    except (OSError, ValueError, TypeError, AttributeError):
        return None


class BrowserApprovals:
    """Only actual prepare_action responses may create host approval candidates."""

    def __init__(self):
        self._lock = Lock()
        self._pending = {}

    def remember(self, definition, arguments, result):
        token = configuration_token(definition)
        data = result.data.get('structuredContent') if result.success and isinstance(result.data,dict) else None
        if token is None or not isinstance(data,dict) or data.get('kind') != 'browser_proposal':
            return
        fingerprint = data.get('fingerprint')
        if not isinstance(fingerprint,str) or not re.fullmatch('[a-f0-9]{64}',fingerprint):
            return
        expected = {'url':arguments.get('url'), 'form_selector':arguments.get('form_selector'),
                    'fields':arguments.get('fields',{}), 'expected_fingerprint':fingerprint}
        if data.get('fields') != expected['fields'] or data.get('selector') != expected['form_selector']:
            return
        with self._lock:
            self._pending = {key:value for key,value in self._pending.items() if value[0]>monotonic()}
            if len(self._pending)>=8:
                self._pending.pop(next(iter(self._pending)))
            self._pending[(definition.name,fingerprint)] = (monotonic()+60,token,deepcopy(expected),deepcopy(data))

    def match(self, definition, arguments, *, consume=False):
        if not isinstance(arguments,dict):
            return None
        fingerprint = arguments.get('expected_fingerprint')
        if not isinstance(fingerprint,str):
            return None
        token = configuration_token(definition)
        with self._lock:
            key = (definition.name,fingerprint)
            stored = self._pending.get(key)
            if stored is None:
                return None
            expires, previous_token, expected, data = stored
            if expires < monotonic() or token is None or token != previous_token:
                self._pending.pop(key,None)
                return None
            if arguments != expected:
                return None
            if consume:
                self._pending.pop(key,None)
            return deepcopy(data)

    def clear(self, server_id):
        with self._lock:
            self._pending = {key:value for key,value in self._pending.items() if key[0] != server_id}
