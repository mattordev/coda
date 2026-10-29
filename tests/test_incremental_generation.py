"""Visible events precede completion without exposing reasoning or replaying output."""
import json
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from ai.providers import openai, gemini, grok, openrouter, llamacpp, ollama
from ai.providers.cancellable import CancellationScope
from ai.providers.completion import CompletionState, PARTIAL_STREAM_ERROR
from ai.providers.generation import GenerationOptions, call_generation
from ai.llm_router import core as router


class IncrementalGenerationTests(unittest.TestCase):
    def test_visible_stream_requires_terminal_marker_even_without_token_cap(self):
        with self.assertRaisesRegex(ValueError, "before completion"):
            CompletionState(GenerationOptions(on_visible_text=lambda text: None)).validate()

    def test_sdk_adapters_emit_only_visible_deltas_before_completion(self):
        for adapter in (openai, gemini, grok, openrouter):
            with self.subTest(adapter=adapter.__name__):
                emitted = []
                def chunks():
                    yield NS(choices=[NS(delta=NS(content=None, reasoning_content="private"), finish_reason=None)])
                    yield NS(choices=[NS(delta=NS(content="Hello "), finish_reason=None)])
                    self.assertEqual(emitted, ["Hello "])
                    yield NS(choices=[NS(delta=NS(content="world."), finish_reason="stop")])
                client = MagicMock()
                client.chat.completions.create.return_value = chunks()
                result = adapter._generate_stream(client, "fixture", [], Event(), CancellationScope(),
                                                 options=GenerationOptions(on_visible_text=emitted.append))
                self.assertEqual(result, "Hello world.")
                self.assertEqual(emitted, ["Hello ", "world."])

    def test_native_adapters_emit_visible_content_only(self):
        for adapter in (llamacpp, ollama):
            with self.subTest(adapter=adapter.__name__):
                emitted = []
                payloads = ([{"choices": [{"delta": {"reasoning_content": "secret"}}]},
                             {"choices": [{"delta": {"content": "Answer."}, "finish_reason": "stop"}]}]
                            if adapter is llamacpp else
                            [{"message": {"thinking": "secret"}},
                             {"message": {"content": "Answer."}, "done": True}])
                http = MagicMock()
                response = http.post.return_value.__enter__.return_value
                response.iter_lines.return_value = [json.dumps(p) for p in payloads]
                result = adapter._generate_stream(http, "http://fixture", "fixture", [], 10,
                                                 Event(), CancellationScope(),
                                                 options=GenerationOptions(on_visible_text=emitted.append))
                self.assertEqual(result, ("Answer.", None))
                self.assertEqual(emitted, ["Answer."])

    def test_late_and_cancelled_callbacks_are_rejected(self):
        captured = []
        emitted = []
        cancel = Event()
        def generate(messages, cancel_event=None, *, options=None):
            captured.append(options.on_visible_text)
            options.on_visible_text("prefix")
            cancel.set()
            with self.assertRaises(InterruptedError):
                options.on_visible_text("cancelled")
            return None, "Request cancelled."
        call_generation(generate, [], cancel_event=cancel,
                        options=GenerationOptions(on_visible_text=emitted.append))
        cancel.clear()
        with self.assertRaises(InterruptedError):
            captured[0]("late")
        self.assertEqual(emitted, ["prefix"])

    def test_router_does_not_replay_after_visible_prefix(self):
        emitted = []
        options = GenerationOptions(on_visible_text=emitted.append)
        def call(*args, **kwargs):
            kwargs["options"].on_visible_text("Already spoken.")
            return None, "connection lost", False
        with patch.object(router, "_call_provider_with_health", side_effect=call) as mocked:
            result = router._try_providers(["first", "second"], "hello", 0, options=options)
        self.assertEqual(result, (None, PARTIAL_STREAM_ERROR))
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(emitted, ["Already spoken."])

    def test_pre_output_failure_still_falls_back(self):
        options = GenerationOptions(on_visible_text=lambda text: None)
        with patch.object(router, "_call_provider_with_health", side_effect=[
            (None, "failed", False), ("answer", None, False),
        ]) as mocked, patch.object(router.logger, "log_failure"):
            self.assertEqual(router._try_providers(["first", "second"], "hello", 0,
                                                  options=options), ("answer", None))
        self.assertEqual(mocked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
