"""Execution grants come from operator files, never model-selected argv."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime.system_policy import load_system_policy, SystemPolicyError


class SystemPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.path = self.root/'policy.json'
        self.action = {'executable':str(Path(sys.executable).resolve()), 'argv':['--version'], 'cwd':str(self.root)}

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')
        return load_system_policy(self.path)

    def test_empty_policy_grants_nothing(self):
        policy = self.write({})
        with self.assertRaises(SystemPolicyError): policy.action('anything')

    def test_exact_operator_argv_and_separate_modes(self):
        policy = self.write({'enabled':True,'actions':{'python-version':self.action}})
        self.assertEqual(policy.action('python-version').argv, ('--version',))
        for kwargs in ({'background':True},{'scheduled':True}):
            with self.assertRaises(SystemPolicyError): policy.action('python-version',**kwargs)
        with self.assertRaises(SystemPolicyError): policy.action('python-version; arbitrary')

    def test_invalid_policy_cannot_introduce_shell_or_model_rules(self):
        for change in ({'shell':True},{'argv':'--version'},{'timeout_seconds':float('nan')},
                       {'background':'yes'},{'executable':'python'},{'argv':['bad\0arg']}):
            with self.subTest(change=change),self.assertRaises((SystemPolicyError,ValueError)):
                self.write({'enabled':True,'actions':{'test':{**self.action,**change}}})

    def test_environment_change_invalidates_durable_authority(self):
        self.action['env'] = {'PROBE':'CODA_POLICY_PROBE'}
        with patch.dict(os.environ, {'CODA_POLICY_PROBE':'before'}):
            first = self.write({'enabled':True,'actions':{'test':self.action}})
        with patch.dict(os.environ, {'CODA_POLICY_PROBE':'after'}):
            second = load_system_policy(self.path)
        self.assertNotEqual(first.fingerprint,second.fingerprint)

    def test_reviewed_script_content_changes_invalidate_authority(self):
        script = self.root/'probe.py'; script.write_text('print("before")')
        self.action.update(argv=[str(script)], files=[str(script)])
        first = self.write({'enabled':True,'actions':{'test':self.action}})
        script.write_text('print("after")')
        second = load_system_policy(self.path)
        self.assertNotEqual(first.fingerprint,second.fingerprint)


if __name__ == '__main__': unittest.main()
