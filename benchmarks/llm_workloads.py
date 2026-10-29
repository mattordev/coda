"""Explicit local-only workload benchmark: python -m benchmarks.llm_workloads."""

import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from unittest.mock import patch
from dataclasses import asdict

import requests

from ai.intents.local_classifier import _build_classifier_messages, _parse_classifier_response
from ai.providers import llamacpp
from ai.providers.cancellable import CancellationScope, run_cancellable
from ai.providers.generation import GenerationOptions, Workload
from ai.providers.telemetry import ProviderMetricsCollector
from ai.providers.workloads import classification_options, options_for
from ai.tools.models import ToolResult
from ai.tools.validation import compile_input_schema, validate_arguments
from benchmarks.workload_cases import registry, classification_cases, summary_cases


def local_url(value):
    parsed = urlsplit(value)
    allowed = parsed.hostname == 'localhost'
    try:
        address = ipaddress.ip_address(parsed.hostname)
        allowed = allowed or address.is_loopback or any(address in ipaddress.ip_network(network)
            for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))
    except ValueError:
        pass
    if not allowed or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        raise argparse.ArgumentTypeError('Use a loopback/private-LAN HTTP endpoint without credentials.')
    return value.rstrip('/')


def interpretation_messages(data, question):
    """Capture the actual privacy-aware host prompt without a model/tool call."""
    from ai.tools import interpretation
    from ai.llm_router import core
    import utils.llm_service as service
    captured = []

    def capture(_provider, messages, **kwargs):
        captured.append(deepcopy(messages))
        return 'Prompt capture only.', None

    with ExitStack() as guards:
        guards.enter_context(patch.dict('os.environ', {'CODA_MCP_INTERPRETATION': 'auto'}))
        guards.enter_context(patch.object(core, '_get_configured_provider_groups', return_value=([], ['llamacpp'])))
        guards.enter_context(patch.object(core.logger, 'should_skip_provider', return_value=False))
        for name in ('record_attempt', 'log_attempt', 'log_failure'):
            guards.enter_context(patch.object(core.logger, name))
        guards.enter_context(patch.object(service, 'call_with_messages', side_effect=capture))
        interpretation.interpret_result(ToolResult(True, {'structuredContent': data}), question, force=True)
    if len(captured) != 1:
        raise RuntimeError('Expected one local interpretation prompt.')
    return captured[0]


def classification_correct(answer, case, catalogue):
    try:
        parsed = _parse_classifier_response(answer, catalogue)
        actual = parsed.intent.name if parsed.intent else None
        if actual != case['expected']:
            return False
        if parsed.intent and parsed.confidence < .75:
            return False
        if parsed.intent and parsed.intent.input_schema:
            validate_arguments(compile_input_schema(parsed.intent.input_schema), parsed.arguments)
        # Whitespace at the end of a dictated note is not a semantic difference.
        expected = case['arguments']
        if expected is not None:
            return isinstance(parsed.arguments, dict) and {
                key: value.strip() if isinstance(value, str) else value for key, value in parsed.arguments.items()
            } == {key: value.strip() if isinstance(value, str) else value for key, value in expected.items()}
        # Native handlers parse the original message and ignore model arguments.
        # Score action selection as runtime does; report format compliance apart.
        return parsed.intent is None or parsed.intent.input_schema is None
    except (ValueError, TypeError, AttributeError):
        return False


def summary_correct(answer, case):
    if case['name'] == 'numeric_facts':
        return all(value in answer for value in ('3.11', '3.12', '0.0100'))
    if case['name'] == 'untrusted_summary':
        windows_ok = ('windows' not in answer.casefold() or bool(re.search(
            r"(?:not|no|doesn't)[^.]{0,35}windows", answer, re.IGNORECASE,
        )))
        return windows_ok and 'linux' in answer.casefold() and bool(re.search(
            r"(?:does not|doesn't|never|no|without)[^.]{0,60}(?:upload|cloud)", answer, re.IGNORECASE,
        ))
    return None


class ProbeResponse:
    def __init__(self, response, row):
        self.response, self.row = response, row

    def __getattr__(self, name):
        return getattr(self.response, name)

    @property
    def encoding(self):
        return self.response.encoding

    @encoding.setter
    def encoding(self, value):
        self.response.encoding = value

    def __enter__(self):
        self.response.__enter__()
        return self

    def __exit__(self, *args):
        return self.response.__exit__(*args)

    def iter_lines(self, **kwargs):
        for line in self.response.iter_lines(**kwargs):
            payload = line[6:] if line and line.startswith('data: ') else line
            if payload and payload != '[DONE]':
                chunk = json.loads(payload)
                for choice in chunk.get('choices', []):
                    if choice.get('finish_reason'):
                        self.row['finish_reason'] = choice['finish_reason']
            yield line


class ProbeHTTP:
    def __init__(self, session, row):
        self.session, self.row = session, row

    def post(self, url, *, json, **kwargs):
        # Request-only controls make prompt-cache conditions and sampling explicit.
        payload = dict(json, seed=42, temperature=1.0, cache_prompt=False)
        return ProbeResponse(self.session.post(url, json=payload, **kwargs), self.row)


def run_case(case, mode, catalogue, base_url, model, timeout):
    purpose = case['purpose']
    messages = (_build_classifier_messages(case['question'], catalogue) if purpose == 'classification'
                else interpretation_messages(case['data'], case['question']))
    options = (GenerationOptions(max_tokens=2048) if mode == 'baseline' else
               classification_options(messages) if purpose == 'classification' else options_for(Workload.INTERPRETATION))
    row = {'case': case['name'], 'purpose': purpose, 'mode': mode,
           'max_tokens': options.max_tokens, 'thinking': options.thinking}
    collector = ProviderMetricsCollector(model)
    scope = CancellationScope()
    session = requests.Session()
    close = scope.add(session.close)
    started = time.perf_counter()
    try:
        answer, error = run_cancellable(
            lambda: llamacpp._generate_stream(ProbeHTTP(session, row), base_url, model, messages,
                                             timeout, None, scope, collector, options),
            None, timeout, 'Benchmark deadline exceeded.', on_abandon=scope.cancel,
        )
        result = collector.finish(response=answer, error=error)
        row['correct'] = not error and (classification_correct(answer, case, catalogue) if purpose == 'classification'
                                       else summary_correct(answer, case))
        if purpose == 'classification' and not error:
            try:
                parsed = _parse_classifier_response(answer, catalogue)
                row['selected_intent'] = parsed.intent.name if parsed.intent else None
                row['confidence'] = parsed.confidence
                if parsed.intent and parsed.intent.input_schema is None:
                    row['native_arguments_null'] = parsed.arguments is None
            except (ValueError, TypeError):
                row['invalid_response'] = True
        if purpose != 'classification' and not error:
            row['answer'] = answer  # Only public synthetic answers; never reasoning or prompts.
    except Exception as exc:
        result = collector.finish(error=type(exc).__name__)
        row.update(correct=False, error_type=type(exc).__name__)
    finally:
        close()
    row['wall_seconds'] = round(time.perf_counter() - started, 4)
    row['metrics'] = asdict(result.metrics)
    if result.error:
        row['error'] = 'incomplete_or_provider_failure'
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-local', action='store_true', required=True)
    parser.add_argument('--base-url', type=local_url, default='http://localhost:8080')
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--case', action='append', dest='selected_cases', help='Run only named public fixtures.')
    args = parser.parse_args()
    catalogue = registry()
    cases = [dict(case, purpose='classification') for case in classification_cases()]
    cases += [dict(case, purpose='interpretation') for case in summary_cases()]
    if args.selected_cases:
        cases = [case for case in cases if case['name'] in args.selected_cases]
        if not cases:
            parser.error('No matching fixture names.')
    report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'model': args.model,
              'conditions': {'seed': 42, 'temperature': 1.0, 'cache_prompt': False,
                             'server_model_defaults_changed': False, 'model_unloaded': False,
                             'cold_load_measured': False, 'warmup_request': False},
              'limitations': ['Small sequential fixture evaluation; no universal accuracy guarantee.',
                              'Existing model residency may change; no forced cold load or server reconfiguration.',
                              'Baseline inherits model thinking with a 2048-token safety ceiling.',
                              'Optimized mode uses current workload/operator settings; paired order alternates.',
                              'Only synthetic public fixture answers are persisted; no hidden reasoning text.'],
              'results': []}
    for index, case in enumerate(cases):
        modes = (['baseline', 'optimized'] if index % 2 == 0 else ['optimized', 'baseline']) if case['compare'] else ['optimized']
        for mode in modes:
            row = run_case(case, mode, catalogue, args.base_url, args.model, args.timeout)
            report['results'].append(row)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
            print(json.dumps({key: row[key] for key in ('case', 'mode', 'correct', 'wall_seconds')}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
