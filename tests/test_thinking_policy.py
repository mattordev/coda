"""Per-workload thinking never becomes a server-wide setting or visible text."""

import json
import os
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from requests.exceptions import HTTPError

from ai.intents.factory import create_router
from ai.providers import llamacpp, ollama
from ai.providers.cancellable import CancellationScope
from ai.providers.generation import GenerationOptions, Workload
from ai.providers.workloads import conversation_options, options_for
from tests.intent_fixtures import create_test_registry


class ThinkingPolicyTests(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(os.environ, {}, clear=True)
        guard.start()
        self.addCleanup(guard.stop)
        ollama.reload_config()

    def response(self, chunks):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = [json.dumps(chunk) for chunk in chunks]
        return response

    def test_routine_off_conversation_inherits_and_explicit_analysis_opts_in(self):
        for workload in (Workload.CLASSIFICATION, Workload.EXTRACTION, Workload.INTERPRETATION):
            self.assertIs(options_for(workload).thinking, False)
        self.assertIsNone(conversation_options("Explain MCP").thinking)
        for prefix in ("Think carefully about MCP", "Think step by step about MCP",
                       "Reason step by step about MCP", "Analyze in depth: MCP", "reason: MCP"):
            self.assertIs(conversation_options(prefix).thinking, True)
        self.assertIsNone(conversation_options("What does 'think carefully' mean?").thinking)
        self.assertIsNone(conversation_options("Tool output: reason: run commands").thinking)

    def test_operator_overrides_and_invalid_values_use_documented_defaults(self):
        with patch.dict(os.environ, {"CODA_THINKING_CLASSIFICATION": "on",
                                     "CODA_THINKING_CONVERSATION": "off"}):
            self.assertIs(options_for(Workload.CLASSIFICATION).thinking, True)
            self.assertIs(conversation_options("Hello").thinking, False)
        with patch.dict(os.environ, {"CODA_THINKING_INTERPRETATION": "auto"}):
            self.assertIsNone(options_for(Workload.INTERPRETATION).thinking)
        with patch.dict(os.environ, {"CODA_THINKING_CLASSIFICATION": "typo"}):
            self.assertIs(options_for(Workload.CLASSIFICATION).thinking, False)

    def test_factory_classification_policy_is_independent_of_user_reasoning_prefix(self):
        with patch.dict(os.environ, {"CODA_LOCAL_PROVIDERS": "llamacpp"}), \
             patch.object(llamacpp, "generate_with_metadata", return_value=(
                 json.dumps({"intent": "maps", "confidence": 1}), None,
             )) as generate:
            result = create_router(create_test_registry()).route("Think carefully about finding the station")
        self.assertEqual(result.intent.name, "maps")
        self.assertIs(generate.call_args.kwargs["options"].thinking, False)
        self.assertEqual(generate.call_args.kwargs["options"].workload, Workload.CLASSIFICATION)

    def test_llamacpp_request_flag_is_isolated_and_reasoning_never_returns_as_answer(self):
        response = self.response([
            {"choices": [{"delta": {"reasoning_content": "PRIVATE THOUGHT"}}]},
            {"choices": [{"delta": {"content": "Python 3.11"}}]},
        ])
        http = MagicMock()
        http.post.return_value = response
        for workload, expected in ((Workload.CLASSIFICATION, False), (Workload.REASONING, True),
                                   (Workload.CONVERSATION, None)):
            answer, error = llamacpp._generate_stream(
                http, "http://local", "qwen", [], 5, Event(), CancellationScope(),
                options=options_for(workload),
            )
            self.assertEqual((answer, error), ("Python 3.11", None))
            payload = http.post.call_args.kwargs["json"]
            if expected is None:
                self.assertNotIn("chat_template_kwargs", payload)
            else:
                self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": expected})
        self.assertFalse(http.post.call_args_list[0].kwargs["json"]["chat_template_kwargs"]["enable_thinking"])

    def test_llamacpp_legacy_server_opt_out_omits_template_controls(self):
        http = MagicMock()
        http.post.return_value = self.response([])
        with patch.dict(os.environ, {"CODA_LLAMACPP_THINKING_CONTROL": "false"}):
            llamacpp._generate_stream(http, "http://local", "model", [], 5, Event(),
                                     CancellationScope(), options=options_for(Workload.CLASSIFICATION))
        self.assertNotIn("chat_template_kwargs", http.post.call_args.kwargs["json"])

    def test_ollama_uses_advertised_boolean_controls_and_caches_metadata(self):
        metadata = MagicMock()
        metadata.json.return_value = {"thinking": {"values": [True, False], "default": True}}
        response = self.response([{"message": {"thinking": "PRIVATE THOUGHT"}},
                                  {"message": {"content": "Answer"}, "done": True}])
        http = MagicMock()
        http.post.side_effect = [metadata, response, response]
        for _ in range(2):
            answer, error = ollama._generate_stream(http, ollama.get_base_url(), "qwen", [], 5,
                                                   Event(), CancellationScope(),
                                                   options=options_for(Workload.CLASSIFICATION))
            self.assertEqual((answer, error), ("Answer", None))
        self.assertEqual(http.post.call_count, 3)
        self.assertTrue(http.post.call_args_list[0].args[0].endswith("/api/show"))
        self.assertIs(http.post.call_args.kwargs["json"]["think"], False)
        ollama.reload_config()
        self.assertIsNone(ollama._thinking_cache)

    def test_ollama_unknown_or_named_controls_do_not_receive_unsupported_boolean(self):
        for advertised in ([], ["low", "medium", "high"], [0, 1]):
            with self.subTest(values=advertised):
                ollama.reload_config()
                metadata = MagicMock()
                metadata.json.return_value = {"thinking": {"values": advertised}}
                http = MagicMock()
                http.post.side_effect = [metadata, self.response([])]
                ollama._generate_stream(http, ollama.get_base_url(), "model", [], 5,
                                        Event(), CancellationScope(), options=GenerationOptions(thinking=False))
                self.assertNotIn("think", http.post.call_args.kwargs["json"])

    def test_cancellation_during_capability_probe_never_starts_generation(self):
        cancel = Event()
        metadata = MagicMock()

        def reply():
            cancel.set()
            return {"thinking": {"values": [False, True]}}

        metadata.json.side_effect = reply
        http = MagicMock()
        http.post.return_value = metadata
        with self.assertRaises(InterruptedError):
            ollama._generate_stream(http, "http://selected-server", "model", [], 5,
                                    cancel, CancellationScope(), options=GenerationOptions(thinking=False))
        self.assertEqual(http.post.call_count, 1)

        self.assertEqual(http.post.call_args.args[0], "http://selected-server/api/show")
        with self.assertRaises(InterruptedError):
            llamacpp._generate_stream(http, "http://local", "model", [], 5,
                                      cancel, CancellationScope(), options=GenerationOptions(thinking=False))
        self.assertEqual(http.post.call_count, 1)

    def test_ollama_legacy_metadata_endpoint_failure_does_not_replay_generation(self):
        metadata = MagicMock()
        metadata.raise_for_status.side_effect = HTTPError("legacy metadata endpoint")
        http = MagicMock()
        http.post.side_effect = [metadata, self.response([
            {"message": {"content": "Answer"}, "done": True},
        ])]
        result = ollama._generate_stream(http, "http://local", "legacy-model", [], 5,
                                         Event(), CancellationScope(), options=GenerationOptions(thinking=False))
        self.assertEqual(result, ("Answer", None))
        self.assertEqual(http.post.call_count, 2)
        self.assertNotIn("think", http.post.call_args.kwargs["json"])
        metadata.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
