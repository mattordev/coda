"""Regression cases taken from the user's v1.5.7 spoken smoke transcript."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai.intents import Intent, IntentRegistry
from ai.intents.local_classifier import LocalClassifierStrategy, _build_classifier_messages
from ai.mcp.web_sources import plan_sources
from ai.tools.captures import retain_capture
from ai.tools.context import prepare_tool_context
from ai.tools.interpretation import interpret_result, is_result_follow_up
from ai.tools.models import ToolResult
from ai.tools.presentation import prepare_tool_display
from utils.retrieval_evidence import evidence_cards, compare_evidence
from utils.web_requests import browser_request, normalize_spoken, public_url


class VoiceWebRegressionTests(unittest.TestCase):
    def test_browser_voice_dispatch_bypasses_classifier_and_honours_instance(self):
        import utils.on_command as commands
        catalog=Mock()
        catalog.manager.inspect.return_value={'renderer':{'bundle':'browser','enabled':True,'trusted':True}}
        executor=catalog.manager.executor.return_value
        executor._intent=Intent('mcp.renderer.inspect_page','Inspect page.',source='mcp')
        with patch.object(commands,'_mcp_catalog',catalog),patch.object(commands,'_pending_confirmation',None),patch.object(commands,'_pending_source_read',None),patch.object(commands,'_tool_outcome',return_value=commands.CommandResult(True)),patch.object(commands,'_dispatch_intent') as classify:
            commands._on_command('Coder, take a browser screenshot of docs.python.org',{})
        classify.assert_not_called()
        catalog.manager.executor.assert_called_once_with('mcp.renderer.inspect_page')
        self.assertEqual(executor.execute.call_args.args[0].arguments,{'url':'https://docs.python.org','capture':True})

    def test_spoken_variants_reach_the_existing_source_grammar(self):
        self.assertEqual(normalize_spoken('Um, go to summarise it.'),'summarise it.')
        self.assertEqual(normalize_spoken('Coda summarized the first source.'),'summarise the first source.')
        request=normalize_spoken("Uh, code of research whether HTMLParser checks closing tags")
        plan=plan_sources(request,None,Mock())
        self.assertEqual(plan.kind,'research')
        self.assertIn('closing tags',plan.query)

    def test_browser_voice_requests_are_explicit_and_do_not_invent_context(self):
        for message,capture in [('Coder, use the browser to inspect docs.python.org.',False),
                                ('Coda, take a browser screenshot of docs dot python dot org.',True)]:
            result=browser_request(message)
            self.assertEqual(result['url'],'https://docs.python.org')
            self.assertEqual(result['capture'],capture)
        self.assertIsNone(browser_request('What does a browser screenshot mean?'))
        self.assertIsNone(browser_request('take a screenshot of that page'))
        self.assertEqual(public_url('http://127.0.0.1/'),'http://127.0.0.1/')
        self.assertEqual(public_url('https semicolon forward slash forward slash docs.python.org'),'https://docs.python.org')
        self.assertEqual(public_url('user:password@example.com'),'user:password@example.com')
        self.assertEqual(public_url('https://example.com/file.'),'https://example.com/file.')
        self.assertEqual(public_url('https://example.com/search?q=what?'),'https://example.com/search?q=what?')

    def test_headings_are_not_evidence_but_short_literal_facts_are(self):
        page={'kind':'web_page','text':'html.parser — Simple HTML and XHTML parser\nPython »\nThis parser does not check that end tags match start tags.'}
        cards=evidence_cards(page,'summarise it')
        self.assertEqual(len(cards),1)
        self.assertIn('does not check',cards[0]['text'])
        self.assertEqual(evidence_cards({'kind':'web_page','text':'PEP 833 — Freezing the HTML simple repository API'},'summarise it'),[])

    def test_comparison_labels_failed_empty_and_available_sources(self):
        result={'kind':'web_sources','sources':[{'status':'failed'},
            {'status':'fetched','text':''},{'status':'fetched','text':'Threads are particularly useful when tasks are I/O bound.'}]}
        answer=compare_evidence(result,'compare the first and second sources')
        self.assertIn('Source 1 could not be read',answer)
        self.assertIn('Source 2 returned no substantive evidence',answer)
        self.assertIn('Source 3: Threads',answer)
        self.assertIn("couldn't obtain enough",answer)
        self.assertIn('no source observations',compare_evidence({'kind':'web_sources','sources':None},'compare sources'))
        self.assertIn('invalid observation',compare_evidence({'kind':'web_sources','sources':['invalid']},'compare sources'))
        self.assertIn('Source 7',compare_evidence({'kind':'web_sources','sources':[{'source_number':7,'status':'failed'}]},'compare sources'))

    def test_connected_false_positive_is_rejected_even_at_high_confidence(self):
        registry=IntentRegistry();registry.register(Intent('connected','Check internet connection.'))
        strategy=LocalClassifierStrategy(lambda _:('{"intent":"connected","confidence":0.9}',None))
        self.assertFalse(strategy.detect('Was that it?',registry).matched)
        self.assertTrue(strategy.detect('Are we online?',registry).matched)
        self.assertTrue(is_result_follow_up('Was that it?'))

    def test_compaction_preserves_schema_constraints_and_property_names(self):
        registry=IntentRegistry()
        schema={'type':'object','description':'large '*1000,'properties':{'title':{'type':'string','maxLength':20,'description':'text '*100}},'required':['title'],'additionalProperties':False}
        original=deepcopy(schema)
        registry.register(Intent('mcp.browser.inspect_page','Inspect a browser page.',source='mcp',input_schema=schema))
        payload=json.loads(_build_classifier_messages('inspect it',registry)[1]['content'])
        compact=payload['available_intents'][0]['input_schema']
        self.assertEqual(compact['properties']['title'],{'type':'string','maxLength':20})
        self.assertEqual(compact['required'],['title'])
        self.assertFalse(compact['additionalProperties'])
        self.assertEqual(schema,original)
        self.assertLess(len(json.dumps(payload)),len(json.dumps(original)))

    def test_capture_is_accessible_bounded_and_never_enters_model_context(self):
        png=b'\x89PNG\r\n\x1a\n'+b'\0'*8+struct.pack('>II',1000,700)
        raw=ToolResult(True,{'structuredContent':{'kind':'browser_page','text':'A captured page.','capture':{'bytes':len(png)}},
                            'content':[{'type':'image','mimeType':'image/png','data':base64.b64encode(png).decode()}]})
        with tempfile.TemporaryDirectory() as directory,patch('ai.tools.captures.capture_directory',return_value=Path(directory)):
            for _ in range(12):saved=retain_capture(raw)
            path=Path(saved.data['structuredContent']['capture']['path'])
            self.assertEqual(path.read_bytes(),png)
            self.assertEqual(len(list(Path(directory).glob('coda-*.png'))),10)
            self.assertIn(str(path),prepare_tool_display(saved))
            self.assertIn(str(path),interpret_result(saved,'Is the tool result saved anywhere?').text)
            self.assertIsNone(prepare_tool_context(saved,for_cloud=True))
            self.assertIsNone(prepare_tool_context(saved,for_cloud=False))
            self.assertNotIn('path',raw.data['structuredContent']['capture'])

    def test_title_extraction_uses_returned_field_without_a_model(self):
        result=ToolResult(True,{'structuredContent':{'kind':'browser_page','title':'Observed title','fields':{'title':'Observed title'},'text':'Other source content.'}})
        with patch('ai.llm_router.core.get_provider_order') as provider:
            answer=interpret_result(result,'extract the page title')
        provider.assert_not_called();self.assertEqual(answer.text,'The page title is Observed title')


if __name__=='__main__':unittest.main()
