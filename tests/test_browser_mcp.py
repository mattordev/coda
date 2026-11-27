"""Disabled browser configuration is inert; observation/image boundaries are explicit."""

import importlib.util
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from ai.tools.context import prepare_tool_context
from ai.tools.interpretation import interpret_result
from ai.tools.models import ToolResult
from ai.mcp.config import discover_definitions
from ai.mcp.transport import SDKStdioTransport
from utils.public_http import NetworkError


class BrowserMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('browser_test_server', root/'integrations/coda-browser/server.py')
        self.server = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.server)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.server.CONFIG_PATH = Path(temporary.name)/'browser.json'

    def configure(self, **extra):
        value = {'enabled': True, 'network': {'allowed_hosts': ['example.com']}}
        value.update(extra)
        self.server.CONFIG_PATH.write_text(json.dumps(value))

    async def test_disabled_missing_and_malformed_configuration_never_import_or_launch_sdk(self):
        before = {n for n in sys.modules if n.startswith('playwright')}
        for raw, code in [('{}','browser_disabled'), ('{','configuration_error'),
                          ('{"enabled":true,"enabled":false}','configuration_error'),
                          ('{"network":{"allowed_hosts":"*"}}','configuration_error'),
                          ('{"limits":{"max_requests":true}}','configuration_error')]:
            self.server.CONFIG_PATH.write_text(raw)
            with patch.object(self.server,'render',new_callable=AsyncMock) as render:
                result = await self.server.inspect_page('https://example.com')
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content['error']['code'], code)
            render.assert_not_awaited()
        self.assertEqual(before,{n for n in sys.modules if n.startswith('playwright')})

    async def test_current_configuration_revocation_discards_result(self):
        self.configure()
        async def rendering(*_, **kwargs):
            self.assertTrue(kwargs['validate']())
            self.configure(enabled=False)
            return {'text':'must not escape'}, None
        with patch.object(self.server,'render',side_effect=rendering):
            result=await self.server.inspect_page('https://example.com')
        self.assertEqual(result.structured_content['error']['code'],'permission_denied')
        self.assertNotIn('must not escape',str(result))

    async def test_capture_stays_in_tool_result_never_model_context(self):
        self.configure()
        page={'kind':'browser_page','text':'Observed','capture':{'bytes':3},'partial':False}
        with patch.object(self.server,'render',return_value=(page,b'PNG')):
            result=await self.server.inspect_page('https://example.com',capture=True)
        self.assertEqual(result.content[1].type,'image')
        tool=ToolResult(True,result.model_dump(by_alias=True,exclude_none=True))
        self.assertIsNone(prepare_tool_context(tool,for_cloud=True))
        self.assertIsNone(prepare_tool_context(tool,for_cloud=False))
        with patch('ai.llm_router.core.get_provider_order') as provider:
            response=interpret_result(tool,'describe it',force=True)
        provider.assert_not_called()
        self.assertIn('captured',response.text)

    async def test_action_observations_never_become_generated_success_claims(self):
        for kind in ('browser_proposal','browser_action'):
            result=ToolResult(True,{'structuredContent':{'kind':kind,'status':204},
                                   'content':[{'type':'text','text':'untrusted '*1000}]})
            with patch('ai.llm_router.core.get_provider_order') as provider:
                response=interpret_result(result,'tell me what happened',force=True)
            provider.assert_not_called()
            self.assertIn('Nothing has been submitted' if kind=='browser_proposal' else 'not been independently verified',response.text)

    async def test_forms_disabled_or_changed_never_submit_and_failed_send_is_not_retried(self):
        self.configure()
        with patch.object(self.server,'render',new_callable=AsyncMock) as render, patch.object(self.server,'post_form') as post:
            result=await self.server.prepare_action('https://example.com','form',{})
        self.assertTrue(result.is_error);render.assert_not_awaited();post.assert_not_called()
        self.configure(actions_enabled=True)
        candidate={'kind':'browser_proposal','fingerprint':'a'*64,'destination':'https://example.com/submit',
                   'body':'message=hello','fields':{'message':'hello'},'selector':'form'}
        async def observed(*_,**__):
            return {'proposal':deepcopy(candidate)},None
        with patch.object(self.server,'render',side_effect=observed), patch.object(self.server,'post_form') as post:
            prepared=await self.server.prepare_action('https://example.com','form',{'message':'hello'})
            fingerprint=prepared.structured_content['fingerprint']
            changed=await self.server.execute_action('https://example.com','form',{'message':'hello'},'b'*64)
            self.assertEqual(changed.structured_content['error']['code'],'form_changed')
            post.assert_not_called()
            post.side_effect=NetworkError('network_timeout','Controlled failed send')
            failed=await self.server.execute_action('https://example.com','form',{'message':'hello'},fingerprint)
            self.assertEqual(failed.structured_content['error']['code'],'action_unverified')
            self.assertEqual(post.call_count,1)

    async def test_actual_sdk_discovery_and_disabled_invoke_without_browser_dependency(self):
        self.configure(enabled=False)
        directory=self.server.CONFIG_PATH.parent/'definitions';directory.mkdir()
        (directory/'browser.json').write_text(json.dumps({'id':'browser','bundled_server':'browser',
            'enabled':True,'trusted':True,'allowed_tools':['inspect_page','prepare_action','execute_action'],
            'settings':{'config_file':str(self.server.CONFIG_PATH)}}))
        definition=discover_definitions(directory).servers[0]
        self.assertIsNone(definition.bundle_error)
        transport=SDKStdioTransport()
        for mode in ('operation','persistent'):
            try:
                current=replace(definition,session_mode=mode)
                found=await asyncio.to_thread(transport.request,current,'discover')
                self.assertTrue(found.success,found.error)
                metadata={tool['name']:tool['annotations'] for tool in found.data['tools']}
                self.assertFalse(metadata['execute_action']['readOnlyHint'])
                result=await asyncio.to_thread(transport.request,current,'invoke',
                    tool_name='inspect_page',arguments={'url':'https://example.com'})
                self.assertFalse(result.success)
                self.assertEqual(result.data['structuredContent']['error']['code'],'browser_disabled')
            finally:
                await asyncio.to_thread(transport.retire,'browser')


if __name__ == '__main__':
    unittest.main()
