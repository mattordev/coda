"""Native forms cannot borrow confirmation across bodies, destinations or grants."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from ai.intents import IntentRequest
from ai.mcp.config import LocalServerDefinition
from ai.mcp.runtime import LocalMCPManager
from ai.tools.models import ToolResult
from utils.browser_forms import proposal
from utils.public_http import NetworkError, NetworkPolicy


class FormProposalTests(unittest.TestCase):
    def setUp(self):
        self.policy=NetworkPolicy(allowed_hosts=('*',))
        self.form={'page':'https://example.com/form','action':'https://example.com/submit',
                   'method':'POST','enctype':'application/x-www-form-urlencoded','dom':'<form>Observed</form>',
                   'controls':[{'name':'message','value':'before','type':'text'},
                               {'name':'nonce','value':'fixed','type':'hidden'}]}

    def test_exact_observation_destination_and_body_change_fingerprint(self):
        original=proposal(self.form,'form',{'message':'hello world'},self.policy)
        self.assertEqual(original['body'],'message=hello+world&nonce=fixed')
        for change in ({'dom':'changed'}, {'action':'https://other.example/submit'},
                       {'controls':[{'name':'message','value':'changed','type':'text'}]}):
            different=proposal({**self.form,**change},'form',{'message':'hello world'},self.policy)
            self.assertNotEqual(original['fingerprint'],different['fingerprint'])
        self.assertNotEqual(original['fingerprint'],proposal(self.form,'form',{'message':'other'},self.policy)['fingerprint'])

    def test_denied_forms_never_produce_an_action_candidate(self):
        for changes, fields in [({'method':'GET'},{}), ({'enctype':'multipart/form-data'},{}),
                ({'action':'http://127.0.0.1/submit'},{}), ({'action':'http://example.com/down'},{}),
                ({},{'nonce':'changed'}), ({},{'missing':'x'}),
                ({'controls':[{'name':'password','value':'secret','type':'password'}]},{}),
                ({'controls':self.form['controls']*2}, {})]:
            with self.subTest(changes=changes,fields=fields),self.assertRaises(NetworkError):
                proposal({**self.form,**changes},'form',fields,self.policy)


class BrowserHostApprovalTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.config=Path(temp.name)/'browser.json'
        self.config.write_text(json.dumps({'enabled':True,'actions_enabled':True}))
        self.definition=LocalServerDefinition('browser','unused',trusted=True,bundled_server='browser',
            allowed_tools=frozenset(('prepare_action','execute_action')),
            settings_json=json.dumps({'config_file':str(self.config)}))
        self.args={'url':'https://example.com/form','form_selector':'form','fields':{'message':'hello'}}
        fingerprint='a'*64+'.'+str(int(time.time()*1000)+60000)
        self.action={**self.args,'expected_fingerprint':fingerprint}
        self.candidate={'kind':'browser_proposal','fingerprint':fingerprint,'selector':'form',
                        'fields':{'message':'hello'},'destination':'https://example.com/submit','body':'message=hello'}
        props={'url':{'type':'string'},'form_selector':{'type':'string'},'fields':{'type':'object'}}
        self.transport=Mock()
        def request(_definition,operation,**kwargs):
            if operation=='discover':
                tools=[]
                for name in ('prepare_action','execute_action'):
                    current=deepcopy(props)
                    if name=='execute_action':current['expected_fingerprint']={'type':'string'}
                    tools.append({'name':name,'inputSchema':{'type':'object','properties':current,
                        'required':list(current),'additionalProperties':False}})
                return ToolResult(True,{'tools':tools})
            if kwargs['tool_name']=='prepare_action':
                return ToolResult(True,{'structuredContent':deepcopy(self.candidate)})
            return ToolResult(True,{'structuredContent':{'kind':'browser_action','status':200}})
        self.transport.request.side_effect=request
        self.manager=LocalMCPManager([self.definition],transport=self.transport)
        self.assertTrue(self.manager.discover('browser').success)

    def execute(self,name,args,confirmed=False):
        executor=self.manager.executor('mcp.browser.'+name)
        return executor.execute(IntentRequest(intent=executor._intent,message='Explicit user test',confidence=1,arguments=args),confirmed=confirmed)

    def prepare(self):
        self.assertTrue(self.execute('prepare_action',self.args).success)

    def test_confirmation_mandatory_even_if_operator_omits_confirm_tools_then_one_use(self):
        self.prepare()
        count=self.transport.request.call_count
        outcome=self.execute('execute_action',self.action)
        self.assertEqual(outcome.error.code,'confirmation_required')
        self.assertEqual(outcome.data['browser_action']['body'],'message=hello')
        self.assertEqual(self.transport.request.call_count,count)
        self.assertTrue(self.execute('execute_action',self.action,confirmed=True).success)
        self.assertEqual(self.execute('execute_action',self.action,confirmed=True).error.code,'approval_missing')

    def test_forged_or_changed_arguments_never_invoke_even_with_confirmed_flag(self):
        self.assertEqual(self.execute('execute_action',self.action,True).error.code,'approval_missing')
        self.prepare()
        count=self.transport.request.call_count
        for change in ({'url':'https://other.example/form'}, {'fields':{'message':'other'}},
                       {'form_selector':'#different'}, {'expected_fingerprint':'b'*64}):
            self.assertEqual(self.execute('execute_action',{**self.action,**change},True).error.code,'approval_missing')
        self.assertEqual(self.transport.request.call_count,count)

    def test_expiry_retirement_and_changed_configuration_invalidate_candidates(self):
        self.prepare()
        with patch('ai.mcp.browser_actions.monotonic',return_value=10**20):
            self.assertEqual(self.execute('execute_action',self.action,True).error.code,'approval_missing')
        self.prepare()
        self.config.write_text(json.dumps({'enabled':True,'actions_enabled':True,'network':{'allowed_hosts':['*']}}))
        self.assertEqual(self.execute('execute_action',self.action,True).error.code,'approval_missing')
        self.prepare()
        self.manager.retire('browser')
        self.assertEqual(self.execute('execute_action',self.action,True).error.code,'approval_missing')


if __name__ == '__main__':
    unittest.main()
