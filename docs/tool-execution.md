# Tool Execution

`Intent` describes a capability, `IntentRequest` represents one invocation,
and `ToolResult` represents its execution outcome. This interface has no MCP
SDK or transport dependency. Actual MCP discovery, sessions, permissions and
runtime integration belong to the later MCP milestone issues.

## Capability metadata and arguments

Existing command declarations keep their existing fields and defaults:

- `source="native"` identifies their origin.
- `input_schema=None` leaves parsing with the native command.

External capabilities declare an explicit source such as `"mcp"` and an
object-valued JSON Schema in `input_schema`. Source labels must be non-empty,
lowercase and free of surrounding whitespace. Future sources can use the
same interface; matching does not branch on source.

`Intent.parameters` remains descriptive metadata used by intent matching.
`input_schema` describes validation constraints, including nested types,
required properties, enums and ranges. Future discovery code should derive
descriptive parameters from the schema rather than maintain two independent
definitions.

`IntentRequest.arguments` contains the structured arguments for one call.
It defaults to `None` for compatibility. The external adapter treats that as
an empty object, which works for tools with no required arguments. The router
selects capabilities; it does not yet extract external arguments from natural
language. Callers must supply arguments, or integrations can supply a custom
preprocessor. Native commands still receive the original request and parse
its message themselves.

## Results and errors

`ToolResult` contains:

- `success`: a required boolean.
- `data`: an optional tool-specific payload.
- `error`: an optional `ToolError` containing a non-blank code and message.

A successful result cannot contain an error. Failures may retain partial data.
Native `False` results can be represented without inventing an explanation.
The dataclasses prevent field reassignment; dictionaries and lists stored in
their fields remain mutable.

Adapters normalize integration-specific responses into this envelope.
`ExternalToolAdapter` expects its invocation callback to return `ToolResult`;
the callback translates any domain-specific response or error. Exceptions
become fixed safe messages without copying raw exception text. Full results
are not automatically logged or added to conversation context. Integrations
must apply CODA's existing privacy policy before logging or exposing
tool-specific payloads and messages to an LLM.

## Shared dispatcher

`IntentDispatcher(commands)` accepts the same native command mapping as
before and wraps each module in `NativeCommandAdapter`. `register(name,
executor)` adds another object implementing `execute(request) -> ToolResult`.
No inheritance is required: the `ToolExecutor` protocol describes this shape.
Registration also checks that `execute` is callable at runtime.

Intent metadata belongs in `IntentRegistry`; the dispatcher owns execution
resolution. Names are normalized with `strip().lower()` in both registries.
Duplicate executor names are rejected rather than replacing an existing tool.

- `execute(request)` returns the full result.
- `dispatch(request)` returns `execute(request).success` for existing callers.
- Missing tools return `tool_not_found`.
- Ordinary invocation exceptions return `execution_error`.
- Invalid executor return values return `invalid_tool_result`.
- Invalid external arguments return `invalid_arguments` before invocation.

The adapters invoke an action once. They do not retry or select an alternative
tool after failure. Shutdown signals such as `KeyboardInterrupt` propagate.
Registration is an explicit programming operation, not a trust decision;
future MCP runtime code must enforce permission policy before execution.

## External tool example

```python
from ai.intents import Intent, IntentDispatcher, IntentRegistry, IntentRouter
from ai.tools.external import ExternalToolAdapter
from ai.tools.models import ToolResult


intent = Intent(
    name="printer_status",
    description="Check the workshop printer.",
    aliases=("printer",),
    source="mcp",
    input_schema={
        "type": "object",
        "properties": {"printer_id": {"type": "string"}},
        "required": ["printer_id"],
        "additionalProperties": False,
    },
)


def fake_invoke(arguments: dict[str, object]) -> ToolResult:
    return ToolResult(
        success=True,
        data={"printer_id": arguments["printer_id"], "state": "idle"},
    )


registry = IntentRegistry()
registry.register(intent)
dispatcher = IntentDispatcher({})
dispatcher.register(intent.name, ExternalToolAdapter(intent, fake_invoke))

matched = IntentRouter(registry).route("printer")
request = matched.to_request("printer", arguments={"printer_id": "workshop"})
result = dispatcher.execute(request)
```

The fake invocation requires no MCP server. A real integration callback can
use an SDK internally while returning the same CODA result type.

## Schema validation

Validation uses `jsonschema`. Schemas without a `$schema` declaration use
Draft 2020-12; declared supported dialects use their corresponding validator.
Malformed schemas and unsupported dialects fail during registration or
adapter construction. Input schemas must declare a root `type` of `object`.

The adapter snapshots its schema at construction and arguments before
invocation. Validation does not coerce values or insert schema defaults.
Non-JSON values, non-string object keys and non-finite numbers are rejected.
Local references such as `#/$defs/printer_id` are supported. No reference
retrieval callback is configured: external schema references cannot trigger
network or filesystem access. Unresolvable references fail safely before
invocation.

An optional `preprocess(request)` callback can produce arguments for an
integration needing custom parsing. Its output still passes schema validation.
Schema `format` annotations are not enabled as additional validation checks.

Official documentation:

- [Schema validation](https://python-jsonschema.readthedocs.io/en/stable/validate/)
- [Reference handling](https://python-jsonschema.readthedocs.io/en/stable/referencing/)

## Tests

```text
python -m unittest tests.test_tool_models tests.test_native_tool_adapter tests.test_external_tools tests.test_intent_dispatcher
```
