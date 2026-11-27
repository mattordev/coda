"""Atomic dashboard snapshots survive transient Windows replacement locks."""

import json
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import patch

from utils import dashboard_state as dashboard


class DashboardWriteTests(unittest.TestCase):
    def test_late_cancelled_feedback_cannot_overwrite_the_final_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            cancelled = Event()
            with patch.object(dashboard, '_STATE_FILE', Path(directory) / 'state.json'):
                dashboard.record_ai_response('The final answer.')
                cancelled.set()
                dashboard.record_ai_response('Stale progress.', source='voice_feedback', cancel_event=cancelled)
                state = dashboard.snapshot()
            self.assertEqual(state['last_ai_message'], 'The final answer.')
            self.assertEqual(len(state['ai_messages']), 1)

    def test_transient_replace_denial_retries_and_publishes_complete_state(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'dashboard_state.json'
            target.write_text('{"old": true}', encoding='utf-8')
            real_replace = Path.replace
            staged = []

            def replace(path, destination):
                staged.append(path)
                self.assertNotEqual(path.name, 'dashboard_state.json.tmp')
                if len(staged) < 3:
                    self.assertEqual(json.loads(target.read_text()), {'old': True})
                    raise PermissionError('reader temporarily holds destination')
                return real_replace(path, destination)

            with (patch.object(dashboard, '_STATE_FILE', target),
                  patch.object(Path, 'replace', replace), patch.object(dashboard.time, 'sleep') as sleep):
                dashboard._write_state_to_disk({'new': 'complete'})
            self.assertEqual(json.loads(target.read_text()), {'new': 'complete'})
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_persistent_denial_preserves_original_and_cleans_owned_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'dashboard_state.json'
            target.write_text('{"old": true}', encoding='utf-8')
            with (patch.object(dashboard, '_STATE_FILE', target),
                  patch.object(Path, 'replace', side_effect=PermissionError('permanent denial')) as replace,
                  patch.object(dashboard.time, 'sleep')):
                with self.assertRaises(PermissionError):
                    dashboard._write_state_to_disk({'new': True})
            self.assertEqual(replace.call_count, 5)
            self.assertEqual(json.loads(target.read_text()), {'old': True})
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_serialization_failure_leaves_original_intact_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'dashboard_state.json'
            target.write_text('{"old": true}', encoding='utf-8')
            with patch.object(dashboard, '_STATE_FILE', target):
                with self.assertRaises(TypeError):
                    dashboard._write_state_to_disk({'invalid': object()})
            self.assertEqual(json.loads(target.read_text()), {'old': True})
            self.assertEqual(list(Path(directory).iterdir()), [target])
