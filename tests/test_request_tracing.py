"""One runtime request spans classification, routing and interpretation threads."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai.request_context import request_context, current_request
from ai.providers.cancellable import run_cancellable
from ai.providers.telemetry import ProviderMetricsCollector
from ai.telemetry.aggregates import build_snapshot
from ai.telemetry.models import UsageMetrics


class RequestTracingTests(unittest.TestCase):
    def test_runtime_envelope_id_reaches_command_execution(self):
        import coda_runtime
        from runtime.messages import RuntimeRequest, InputSource
        from runtime.runtime_queue import RuntimeQueues
        queues, stop = RuntimeQueues(), Event()
        request = RuntimeRequest('hello', InputSource.MANUAL)
        observed = []

        def command(*args, **kwargs):
            observed.append(current_request())
            return SimpleNamespace(handled=True, response_text=None, open_follow_up=False)

        with patch.object(coda_runtime, 'runtime_queues', queues), \
             patch.object(coda_runtime.command, 'run', side_effect=command), patch('builtins.print'):
            worker = Thread(target=coda_runtime._execution_loop, args=(stop,))
            worker.start()
            try:
                queues.requests.put(request)
                result = queues.events.get(timeout=2)
                self.assertEqual(result.request_id, request.request_id)
            finally:
                stop.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(observed[0].trace_id, request.request_id)
        self.assertIs(observed[0].cancel_event, request.cancel_event)
        self.assertIsNone(current_request())

    def test_nested_workers_keep_identity_and_cancellation_then_restore_context(self):
        cancel = Event()
        with request_context('runtime-id', cancel) as expected:
            def nested():
                return run_cancellable(current_request, cancel, 2, 'timeout')
            self.assertEqual(run_cancellable(nested, cancel, 2, 'timeout'), expected)
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with request_context('nested'):
                    raise RuntimeError('fixture')
            self.assertEqual(current_request(), expected)
        self.assertIsNone(current_request())

    def test_parallel_requests_never_share_context(self):
        def work(number):
            with request_context(f'request-{number}'):
                return run_cancellable(current_request, None, 2, 'timeout').trace_id
        with ThreadPoolExecutor(max_workers=4) as executor:
            self.assertEqual(list(executor.map(work, range(8))), [f'request-{n}' for n in range(8)])
        self.assertIsNone(current_request())

    def test_classifier_conversation_and_interpretation_attempts_share_trace(self):
        from ai.intents.factory import create_router
        from ai.llm_router import core
        from ai.tools.interpretation import interpret_result
        from ai.tools.models import ToolResult
        from tests.intent_fixtures import create_test_registry
        import utils.llm_service as service

        provider = SimpleNamespace(generate_with_metadata=Mock(return_value=(
            json.dumps({'intent': None, 'confidence': 0}), None,
        )))
        records = []
        observed = []

        def interpret(*args, **kwargs):
            observed.append(current_request().trace_id)
            return 'The source describes MCP.', None

        with request_context('one-runtime-request', Event()), \
             patch('ai.intents.factory._get_local_classifier_provider', return_value=('llamacpp', provider)), \
             patch.object(core.logger, 'record_attempt', side_effect=records.append), \
             patch.object(core.logger, 'should_skip_provider', return_value=False), \
             patch.object(core.logger, 'log_attempt'), \
             patch.object(core, '_get_configured_provider_groups', return_value=([], ['llamacpp'])), \
             patch.object(service, 'call_with_messages', side_effect=interpret), \
             patch.dict(os.environ, {'CODA_MCP_INTERPRETATION': 'auto'}):
            create_router(create_test_registry()).route('Tell me a story about the sea')
            core._try_providers(['llamacpp'], 'Hello', 0, provider_call=lambda *args, **kwargs: ('Hello', None))
            interpret_result(ToolResult(True, {'content': [{'type': 'text', 'text': 'MCP tools are supported. ' * 40}]}),
                             'Summarize this')
        self.assertEqual(len(records), 3)
        self.assertEqual({item.trace_id for item in records}, {'one-runtime-request'})
        self.assertEqual({item.purpose.value for item in records},
                         {'intent_classification', 'conversation', 'tool_interpretation'})
        self.assertEqual(observed, ['one-runtime-request'])
        self.assertNotIn('MCP tools are supported', json.dumps([asdict(item) for item in records]))

    def test_active_classifier_receives_cancel_event_and_releases_work(self):
        from ai.intents.factory import create_router
        from ai.llm_router import core
        from ai.telemetry.models import AttemptOutcome
        from tests.intent_fixtures import create_test_registry
        import utils.on_command as command
        started, released, finished, cancel = Event(), Event(), Event(), Event()

        def generate(messages, cancel_event=None, *, options=None):
            self.assertIs(cancel_event, cancel)

            def work():
                started.set()
                released.wait(2)
                finished.set()
                return 'late answer'

            try:
                return run_cancellable(work, cancel_event, 2, 'timeout', on_abandon=released.set), None
            except InterruptedError:
                return None, 'Request cancelled.'

        provider = SimpleNamespace(generate_with_metadata=generate)
        records = []
        with patch('ai.intents.factory._get_local_classifier_provider', return_value=('llamacpp', provider)), \
             patch.object(core.logger, 'record_attempt', side_effect=records.append):
            router = create_router(create_test_registry())
            with patch.object(command, '_intent_router', router), patch.object(command, '_mcp_catalog', None), \
                 patch.object(command, 'route_request') as fallback, patch('builtins.print'):
                worker = Thread(target=command.on_command,
                                args=('Tell me about the ocean', {}), kwargs={'cancel_event': cancel})
                worker.start()
                try:
                    self.assertTrue(started.wait(1))
                    cancel.set()
                    worker.join(1)
                    self.assertFalse(worker.is_alive())
                    self.assertTrue(finished.wait(1))
                finally:
                    cancel.set()
                    released.set()
                    worker.join(2)
                fallback.assert_not_called()
        self.assertEqual(records[-1].outcome, AttemptOutcome.CANCELLED)


class ExpandedMetricsTests(unittest.TestCase):
    def collector(self, *ticks):
        clock = iter(ticks)
        return ProviderMetricsCollector(clock=lambda: next(clock))

    def test_reasoning_arrival_and_visible_arrival_are_distinct_without_storing_thoughts(self):
        collector = self.collector(10, 11, 14, 16)
        collector.observe_openai_chunk({'choices': [{'delta': {'reasoning_content': 'PRIVATE THOUGHT'}}]})
        collector.observe_content('Python 3.11')
        collector.observe_openai_chunk({'usage': {'prompt_tokens': 100, 'completion_tokens': 80,
                                                'completion_tokens_details': {'reasoning_tokens': 60}},
                                        'timings': {'prompt_ms': 250, 'predicted_ms': 2000}})
        metrics = collector.finish().metrics
        self.assertEqual(metrics.time_to_first_generated_token_seconds, 1)
        self.assertEqual(metrics.time_to_first_visible_token_seconds, 4)
        self.assertEqual(metrics.time_to_first_token_seconds, 4)
        self.assertEqual(metrics.prompt_duration_seconds, .25)
        self.assertEqual(metrics.decode_duration_seconds, 2)
        self.assertEqual(metrics.reasoning_tokens, 60)
        self.assertEqual(metrics.throughput_tokens_per_second, 40)
        self.assertNotIn('PRIVATE THOUGHT', repr(collector.__dict__))

    def test_unknown_reasoning_counts_are_never_inferred_from_output_size(self):
        collector = self.collector(0, 2, 3)
        collector.observe_content('Hi')
        collector.observe_openai_chunk({'usage': {'completion_tokens': 1682}})
        metrics = collector.finish().metrics
        self.assertIsNone(metrics.reasoning_tokens)
        self.assertEqual(metrics.time_to_first_generated_token_seconds, 2)
        self.assertEqual(metrics.time_to_first_visible_token_seconds, 2)

    def test_ollama_native_zero_timings_and_exposed_thinking(self):
        collector = self.collector(0, 1, 2, 4)
        collector.observe_ollama_chunk({'message': {'thinking': 'PRIVATE'}, 'load_duration': 0,
                                       'prompt_eval_duration': 100_000_000, 'eval_duration': 2_000_000_000,
                                       'eval_count': 20, 'prompt_eval_count': 100})
        collector.observe_content('Answer')
        metrics = collector.finish().metrics
        self.assertEqual(metrics.load_duration_seconds, 0)
        self.assertEqual(metrics.prompt_duration_seconds, .1)
        self.assertEqual(metrics.decode_duration_seconds, 2)
        self.assertIsNone(metrics.reasoning_tokens)

    def test_invalid_native_values_are_unknown_and_reported_zero_reasoning_is_real(self):
        collector = self.collector(0, 1)
        collector.observe_openai_chunk({'usage': {'completion_tokens_details': {'reasoning_tokens': 0}},
                                        'timings': {'prompt_ms': float('nan'), 'predicted_ms': True}})
        metrics = collector.finish().metrics
        self.assertIsNone(metrics.prompt_duration_seconds)
        self.assertIsNone(metrics.decode_duration_seconds)
        self.assertEqual(metrics.reasoning_tokens, 0)
        for field in ('prompt_duration_seconds', 'decode_duration_seconds',
                      'time_to_first_generated_token_seconds', 'time_to_first_visible_token_seconds'):
            with self.assertRaises(ValueError):
                UsageMetrics(**{field: float('nan')})
        with self.assertRaises(ValueError):
            UsageMetrics(reasoning_tokens=True)

    def test_old_and_new_persisted_records_aggregate_without_inventing_samples(self):
        base = {'provider': 'llamacpp', 'model': 'qwen', 'purpose': 'intent_classification',
                'outcome': 'success', 'timestamp': 1}
        snapshot = build_snapshot([
            dict(base, metrics={'time_to_first_token_seconds': 3}),
            dict(base, timestamp=2, metrics={'time_to_first_generated_token_seconds': 1,
                                           'time_to_first_visible_token_seconds': 2,
                                           'reasoning_tokens': 0, 'prompt_duration_seconds': .2}),
        ], generated_at=3)
        group = snapshot.aggregates[0]
        self.assertEqual(group.time_to_first_generated_token_seconds.sample_count, 1)
        self.assertEqual(group.time_to_first_visible_token_seconds.average, 2)
        self.assertEqual(group.reasoning_tokens.average, 0)
        self.assertEqual(group.prompt_duration_seconds.average, .2)
        self.assertEqual(group.decode_duration_seconds.sample_count, 0)


if __name__ == '__main__':
    unittest.main()
