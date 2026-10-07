"""Exercise external tools through the same intent and execution interfaces."""

from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from ai.intents.dispatcher import IntentDispatcher
from ai.intents.local_classifier import _build_intent_catalog
from ai.intents.models import Intent, IntentParameter, IntentRequest
from ai.intents.registry import IntentRegistry
from ai.intents.router import IntentRouter
from ai.tools.external import ExternalToolAdapter
from ai.tools.models import ToolError, ToolResult
from ai.tools.native import NativeCommandAdapter
from ai.tools.validation import compile_input_schema


def printer_intent():
    return Intent(
        name="printer_status",
        description="Check a printer's status.",
        source="mcp",
        aliases=("printer",),
        input_schema={
            "type": "object",
            "properties": {"printer_id": {"type": "string", "minLength": 1}},
            "required": ["printer_id"],
            "additionalProperties": False,
        },
    )


class ExternalToolTests(unittest.TestCase):
    def setUp(self):
        self.intent = printer_intent()
        self.invoke = Mock(return_value=ToolResult(success=True, data={"state": "idle"}))
        self.adapter = ExternalToolAdapter(self.intent, self.invoke)

    def request(self, arguments=None):
        return IntentRequest(
            intent=self.intent,
            message="check the workshop printer",
            confidence=1.0,
            arguments=arguments,
        )

    def test_external_intent_routes_by_alias_and_executes_valid_arguments(self):
        registry = IntentRegistry()
        registry.register(self.intent)
        dispatcher = IntentDispatcher({})
        dispatcher.register(self.intent.name, self.adapter)
        request = IntentRouter(registry).route("printer").to_request(
            "printer", arguments={"printer_id": "workshop"}
        )

        result = dispatcher.execute(request)

        self.assertIs(result, self.invoke.return_value)
        self.invoke.assert_called_once_with({"printer_id": "workshop"})

    def test_invalid_arguments_never_reach_the_integration(self):
        for arguments in (
            None, {}, {"printer_id": 12}, {"printer_id": ""},
            {"printer_id": "workshop", "extra": True}, [],
            {"printer_id": "workshop", 12: "invalid key"},
        ):
            with self.subTest(arguments=arguments):
                result = self.adapter.execute(self.request(arguments))
                self.assertFalse(result.success)
                self.assertEqual(result.error.code, "invalid_arguments")
                self.invoke.assert_not_called()

    def test_validation_errors_do_not_expose_sensitive_arguments(self):
        result = self.adapter.execute(self.request({"printer_id": ["secret-token"]}))

        self.assertEqual(result.error.code, "invalid_arguments")
        self.assertNotIn("secret-token", repr(result))

    def test_integration_errors_are_safe_and_not_retried(self):
        self.invoke.side_effect = RuntimeError("secret-token")

        result = self.adapter.execute(self.request({"printer_id": "workshop"}))

        self.assertEqual(result.error.code, "execution_error")
        self.assertNotIn("secret-token", repr(result))
        self.invoke.assert_called_once()

    def test_integration_can_return_a_normalized_domain_error(self):
        expected = ToolResult(
            success=False,
            error=ToolError(code="device_unavailable", message="The printer is offline."),
        )
        self.invoke.return_value = expected

        self.assertIs(
            self.adapter.execute(self.request({"printer_id": "workshop"})), expected
        )

    def test_invalid_integration_result_is_rejected(self):
        self.invoke.return_value = True

        result = self.adapter.execute(self.request({"printer_id": "workshop"}))

        self.assertEqual(result.error.code, "invalid_tool_result")

    def test_wrong_tool_request_does_not_invoke_integration(self):
        request = replace(self.request(), intent=Intent("maps", "Open a map."))

        result = self.adapter.execute(request)

        self.assertEqual(result.error.code, "invalid_request")
        self.invoke.assert_not_called()

    def test_optional_preprocessing_still_goes_through_schema_validation(self):
        preprocess = Mock(return_value={"printer_id": "workshop"})
        adapter = ExternalToolAdapter(self.intent, self.invoke, preprocess=preprocess)
        request = self.request()

        self.assertTrue(adapter.execute(request).success)
        preprocess.assert_called_once_with(request)
        self.invoke.assert_called_once_with({"printer_id": "workshop"})

        self.invoke.reset_mock()
        preprocess.return_value = {"printer_id": 12}
        self.assertEqual(adapter.execute(request).error.code, "invalid_arguments")
        self.invoke.assert_not_called()

    def test_preprocessing_exception_is_safe_and_prevents_invocation(self):
        adapter = ExternalToolAdapter(
            self.intent, self.invoke,
            preprocess=Mock(side_effect=RuntimeError("secret-token")),
        )

        result = adapter.execute(self.request())

        self.assertEqual(result.error.code, "invalid_arguments")
        self.assertNotIn("secret-token", repr(result))
        self.invoke.assert_not_called()

    def test_schema_is_snapshotted_at_adapter_creation(self):
        self.intent.input_schema["properties"]["printer_id"]["type"] = "integer"

        self.assertTrue(
            self.adapter.execute(self.request({"printer_id": "workshop"})).success
        )

    def test_invocation_cannot_mutate_request_arguments(self):
        arguments = {"printer_id": "workshop"}

        def invoke(values):
            values["printer_id"] = "changed"
            return ToolResult(success=True)

        adapter = ExternalToolAdapter(self.intent, invoke)
        adapter.execute(self.request(arguments))

        self.assertEqual(arguments, {"printer_id": "workshop"})

    def test_invocation_cannot_mutate_nested_request_arguments(self):
        intent = replace(self.intent, input_schema={"type": "object"})
        arguments = {"settings": {"layers": [1, 2]}}

        def invoke(values):
            values["settings"]["layers"].append(3)
            return ToolResult(success=True)

        adapter = ExternalToolAdapter(intent, invoke)
        result = adapter.execute(replace(self.request(arguments), intent=intent))

        self.assertTrue(result.success)
        self.assertEqual(arguments, {"settings": {"layers": [1, 2]}})

    def test_no_argument_tool_accepts_missing_arguments_as_empty_object(self):
        intent = replace(self.intent, input_schema={"type": "object", "maxProperties": 0})
        adapter = ExternalToolAdapter(intent, self.invoke)

        self.assertTrue(adapter.execute(replace(self.request(), intent=intent)).success)
        self.invoke.assert_called_once_with({})

    def test_local_schema_references_are_supported(self):
        schema = {
            "type": "object",
            "$defs": {"id": {"type": "string"}},
            "properties": {"printer_id": {"$ref": "#/$defs/id"}},
            "required": ["printer_id"],
        }
        adapter = ExternalToolAdapter(replace(self.intent, input_schema=schema), self.invoke)

        self.assertTrue(adapter.execute(self.request({"printer_id": "workshop"})).success)
        self.assertFalse(adapter.execute(self.request({"printer_id": 12})).success)

    def test_unresolved_remote_reference_does_not_access_the_network(self):
        schema = {
            "type": "object",
            "properties": {"printer_id": {"$ref": "https://example.com/private-schema"}},
        }
        adapter = ExternalToolAdapter(replace(self.intent, input_schema=schema), self.invoke)

        with patch("socket.socket") as socket:
            result = adapter.execute(self.request({"printer_id": "workshop"}))

        self.assertEqual(result.error.code, "invalid_arguments")
        socket.assert_not_called()
        self.invoke.assert_not_called()

    def test_shutdown_signal_is_not_swallowed(self):
        self.invoke.side_effect = KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.adapter.execute(self.request({"printer_id": "workshop"}))

    def test_constructor_rejects_invalid_configuration(self):
        for intent in (
            replace(self.intent, source="native"),
            replace(self.intent, source=""),
            replace(self.intent, input_schema=None),
            replace(self.intent, name=""),
        ):
            with self.subTest(intent=intent):
                with self.assertRaises(ValueError):
                    ExternalToolAdapter(intent, self.invoke)
        with self.assertRaises(TypeError):
            ExternalToolAdapter(self.intent, None)
        with self.assertRaises(TypeError):
            ExternalToolAdapter(self.intent, self.invoke, preprocess=False)


class CapabilityMetadataTests(unittest.TestCase):
    def test_native_defaults_preserve_existing_construction(self):
        intent = Intent("maps", "Open a map.")
        request = IntentRequest(intent, "maps", 1.0)

        self.assertEqual(intent.source, "native")
        self.assertIsNone(intent.input_schema)
        self.assertIsNone(request.arguments)

    def test_native_commands_do_not_receive_universal_argument_validation(self):
        command = Mock()
        command.run.return_value = True
        intent = Intent("maps", "Open a map.")
        request = IntentRequest(intent, "maps", 1.0, arguments={"arbitrary": object()})

        self.assertTrue(NativeCommandAdapter(command).execute(request).success)
        command.run.assert_called_once_with(request)

    def test_invalid_metadata_does_not_partially_register_capability(self):
        intent = printer_intent()
        for invalid in (
            replace(intent, name=None),
            replace(intent, description=" "),
            replace(intent, source=""),
            replace(intent, source=" MCP "),
            replace(intent, input_schema=None),
            replace(intent, aliases=(12,)),
            replace(intent, examples=(False,)),
            replace(intent, parameters=("invalid",)),
            replace(intent, parameters=(IntentParameter(" ", "ID"),)),
            replace(intent, parameters=(IntentParameter("id", "ID", required="yes"),)),
            replace(intent, parameters=(IntentParameter("id", "ID"), IntentParameter(" ID ", "ID"))),
            replace(intent, input_schema={"type": "object", "required": "id"}),
        ):
            with self.subTest(invalid=invalid):
                registry = IntentRegistry()
                with self.assertRaises((ValueError, TypeError)):
                    registry.register(invalid)
                self.assertEqual(registry.all(), ())
                self.assertIsNone(registry.get("printer"))

    def test_sources_other_than_mcp_can_use_the_common_interface(self):
        registry = IntentRegistry()
        registry.register(replace(printer_intent(), source="plugin"))

        self.assertIsNotNone(IntentRouter(registry).route("printer").intent)

    def test_classifier_catalog_does_not_change_based_on_capability_source(self):
        native_registry = IntentRegistry()
        external_registry = IntentRegistry()
        external = printer_intent()
        native_registry.register(replace(external, source="native", input_schema=None))
        external_registry.register(external)

        self.assertEqual(
            _build_intent_catalog(native_registry),
            _build_intent_catalog(external_registry),
        )

    def test_dispatcher_normalizes_names_and_rejects_equivalent_duplicates(self):
        dispatcher = IntentDispatcher({})
        executor = Mock(execute=Mock(return_value=ToolResult(success=True)))
        dispatcher.register(" Printer_Status ", executor)
        with self.assertRaisesRegex(ValueError, "already registered"):
            dispatcher.register("printer_status", executor)
        request = IntentRequest(printer_intent(), "printer", 1.0)

        self.assertTrue(dispatcher.execute(request).success)

    def test_invalid_schema_definitions_are_rejected(self):
        for schema in (
            None, [], {"type": "string"},
            {"type": "object", "$schema": "https://example.com/unsupported"},
            {"type": "object", "$schema": []},
            {"type": "object", "properties": {"id": {"type": "not-a-type"}}},
        ):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError):
                    compile_input_schema(schema)

    def test_declared_supported_schema_dialect_is_used(self):
        validator = compile_input_schema({
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"value": {"type": "integer"}},
        })

        self.assertTrue(validator.is_valid({"value": 1}))
        self.assertFalse(validator.is_valid({"value": "1"}))


class StructuredArgumentTests(unittest.TestCase):
    def test_nested_types_enums_ranges_and_arrays_are_validated(self):
        schema = {
            "type": "object",
            "properties": {
                "settings": {
                    "type": "object",
                    "properties": {
                        "mode": {"enum": ["draft", "normal"]},
                        "copies": {"type": "integer", "minimum": 1, "maximum": 5},
                        "layers": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["mode", "copies", "layers"],
                    "additionalProperties": False,
                },
            },
            "required": ["settings"],
            "additionalProperties": False,
        }
        intent = replace(printer_intent(), input_schema=schema)
        invoke = Mock(return_value=ToolResult(success=True))
        adapter = ExternalToolAdapter(intent, invoke)
        valid = {"settings": {"mode": "draft", "copies": 2, "layers": [1, 2]}}
        request = IntentRequest(intent, "print", 1.0, arguments=valid)
        self.assertTrue(adapter.execute(request).success)
        invoke.reset_mock()

        for field, value in (
            ("mode", "unknown"), ("copies", 0), ("copies", 6),
            ("copies", "2"), ("copies", True), ("layers", ["1"]),
            ("layers", [float("nan")]), ("layers", [object()]),
        ):
            with self.subTest(field=field, value=value):
                arguments = deepcopy(valid)
                arguments["settings"][field] = value
                result = adapter.execute(replace(request, arguments=arguments))
                self.assertEqual(result.error.code, "invalid_arguments")
                invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
