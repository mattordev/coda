"""Unsupported tool protocols cannot become speech, history, or actions."""
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai.providers.answer_text import AnswerTextGuard
from ai.providers.completion import PARTIAL_STREAM_ERROR, UNSUPPORTED_COMPLETION_ERROR
from ai.providers.generation import GenerationOptions, Workload, call_generation
from ai.providers.telemetry import ProviderCallResult
from ai.telemetry.models import UsageMetrics
from ai.llm_router import core as router
import utils.llm_service as service


class AnswerTextTests(unittest.TestCase):
    def test_control_markers_are_blocked_at_every_stream_split(self):
        for marker in ('<|tool_call_start|>', '<|tool_call_end|>', '<tool_call>', '</tool_call>'):
            for split in range(len(marker) + 1):
                with self.subTest(marker=marker, split=split):
                    visible = []
                    def generate(_messages, cancel_event=None, *, options=None):
                        options.on_visible_text(marker[:split])
                        options.on_visible_text(marker[split:] + "[google(query='fixture')]")
                        return marker, None
                    result = call_generation(generate, [], options=GenerationOptions(
                        on_visible_text=visible.append))
                    self.assertEqual(result, (None, UNSUPPORTED_COMPLETION_ERROR))
                    self.assertEqual(visible, [])

    def test_ordinary_code_unicode_and_comparison_signs_are_preserved(self):
        text = "Use google(query='example') in code. Caf\u00e9: x < y; <toolbox>."
        visible = []
        guard = AnswerTextGuard(visible.append)
        for char in text:
            guard.feed(char)
        guard.finish()
        self.assertEqual(''.join(visible), text)
        guard = AnswerTextGuard(visible.append)
        guard.feed('<')
        guard.finish()
        self.assertEqual(visible[-1], '<')

    def test_truncated_markers_are_not_flushed_and_buffer_is_bounded(self):
        visible = []
        def generate(_messages, cancel_event=None, *, options=None):
            options.on_visible_text('<|tool_call_sta')
            return '<|tool_call_sta', None
        self.assertEqual(call_generation(generate, [], options=GenerationOptions(
            on_visible_text=visible.append)), (None, UNSUPPORTED_COMPLETION_ERROR))
        self.assertEqual(visible, [])
        guard = AnswerTextGuard(lambda _text: None)
        for _ in range(100):
            guard.feed('ordinary text ' * 100 + '<')
            self.assertLess(len(guard._pending), 20)

    def test_nonstreaming_and_legacy_results_are_validated_with_metadata_preserved(self):
        original = ProviderCallResult('<tool_call>bad</tool_call>', None, 'fixture', UsageMetrics())
        def legacy(_messages, cancel_event=None):
            return original
        result = call_generation(legacy, [])
        self.assertIsNone(result.response)
        self.assertEqual(result.error, UNSUPPORTED_COMPLETION_ERROR)
        self.assertEqual(result.model, original.model)
        self.assertIs(result.metrics, original.metrics)

    def test_structured_workloads_do_not_rewrite_argument_values(self):
        text = '{"path":"<tool_call>"}'
        for workload in (Workload.CLASSIFICATION, Workload.EXTRACTION):
            visible = []
            def generate(_messages, cancel_event=None, *, options=None):
                options.on_visible_text(text)
                return text, None
            result = call_generation(generate, [], options=GenerationOptions(
                workload=workload, on_visible_text=visible.append))
            self.assertEqual(result, (text, None))
            self.assertEqual(visible, [text])

    def test_cancellation_and_late_callbacks_do_not_flush_held_marker_text(self):
        visible, callbacks, cancel = [], [], Event()
        def generate(_messages, cancel_event=None, *, options=None):
            callbacks.append(options.on_visible_text)
            options.on_visible_text('<|tool')
            cancel.set()
            return None, 'Request cancelled.'
        self.assertEqual(call_generation(generate, [], cancel_event=cancel,
                                         options=GenerationOptions(on_visible_text=visible.append)),
                         (None, 'Request cancelled.'))
        cancel.clear()
        with self.assertRaises(InterruptedError):
            callbacks[0]('_call_start|>')
        self.assertEqual(visible, [])

    def test_router_falls_back_only_before_output_and_rolls_back_failed_history(self):
        for prefix in ('', 'Already spoken. '):
            with self.subTest(prefix=prefix):
                emitted, calls = [], []
                history = [service._new_system_message()]
                def broken(_messages, cancel_event=None, *, options=None):
                    calls.append('broken')
                    if prefix:
                        options.on_visible_text(prefix)
                    options.on_visible_text('<|tool_call_')
                    options.on_visible_text('start|>[google()]')
                    return 'unreachable', None
                def healthy(_messages, cancel_event=None, *, options=None):
                    calls.append('healthy')
                    options.on_visible_text('A supported answer.')
                    return 'A supported answer.', None
                modules = {'broken': SimpleNamespace(generate=broken),
                           'healthy': SimpleNamespace(generate=healthy)}
                with patch.object(service, 'conversation_log', history), \
                     patch.object(service.registry, 'get_provider_module', side_effect=modules.get), \
                     patch.object(service.registry, 'get_provider_type', return_value='local'), \
                     patch.object(router.logger, 'should_skip_provider', return_value=False), \
                     patch.object(router.logger, 'log_attempt'), \
                     patch.object(router.logger, 'record_attempt'), \
                     patch.object(router.logger, 'log_failure') as cooldown:
                    result = router._try_providers(['broken', 'healthy'], 'fixture', 0,
                                                  options=GenerationOptions(on_visible_text=emitted.append))
                if prefix:
                    self.assertEqual(result, (None, PARTIAL_STREAM_ERROR))
                    self.assertEqual(calls, ['broken'])
                    self.assertEqual(emitted, [prefix])
                    self.assertEqual(len(history), 1)
                else:
                    self.assertEqual(result, ('A supported answer.', None))
                    self.assertEqual(calls, ['broken', 'healthy'])
                    self.assertEqual(emitted, ['A supported answer.'])
                    self.assertEqual([item['content'] for item in history[1:]],
                                     ['fixture', 'A supported answer.'])
                cooldown.assert_not_called()
