"""Disabled browser configuration is inert; observation/image boundaries are explicit."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from ai.tools.context import prepare_tool_context
from ai.tools.interpretation import interpret_result
from ai.tools.models import ToolResult


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


if __name__ == '__main__':
    unittest.main()
