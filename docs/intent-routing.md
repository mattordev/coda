# Intent Routing

Intent routing is the part of CODA that works out which command a message is
asking for. It is kept separate from command execution so command modules can
focus on doing the work.

## Startup

CODA sets the router up after loading the command modules:

1. Command files are loaded from `Commands` or from the command cache.
2. Each module is checked for a valid `INTENT` and callable `run` function.
3. The discovered intents are added to an `IntentRegistry`.
4. `create_router()` builds the configured list of detection strategies.
5. The completed router is stored by `utils/on_command.py` for incoming
   messages.

The command cache stores module paths, not a separate copy of the intent
metadata. The module remains the source of truth.

## Request Flow

The default strategy order is:

```text
exact name or alias
        ↓
declared natural-language example
        ↓
command name or alias used as a prefix
        ↓
local classifier, when enabled
```

The rule-based strategies run first because they are quick and predictable.
The local classifier is only used when those rules do not find an accepted
intent.

Each strategy returns an `IntentResult`. The router uses its confidence score
to decide what happens next:

- `0.75` and above is accepted.
- `0.40` to below `0.75` needs clarification.
- Below `0.40` is treated as unmatched.

Rule-based matches currently use a confidence of `1.0`. If more than one
strategy returns a clarification candidate, the router keeps the strongest
one. Equal scores keep the earlier strategy result.

The router can report that a result needs clarification, although the current
runtime does not start a clarification conversation yet. An unaccepted result
continues to the normal LLM fallback when that fallback is enabled.

## Dispatcher

An accepted result is converted into an `IntentRequest`. This carries the
matched intent, original message, confidence score and strategy name.

`IntentDispatcher` then:

1. Looks up the loaded command using the intent's canonical name.
2. Calls the module's `run(request)` function.
3. Converts the command's return value to `True` or `False`.

A command should return `True` when it handled the request and `False` when it
could not complete it. Once an intent has been accepted and dispatched, a
failed command does not fall through to general LLM chat. This avoids running
a different action after the user already matched a command.

## Example

Given this intent:

```python
INTENT = Intent(
    name="connected",
    description="Checks if CODA can reach the internet.",
    aliases=("connectivity", "network status"),
    examples=(
        "Are we connected?",
        "Do we have an internet connection?",
    ),
)
```

These messages all select `connected`, but by different strategies:

```text
connected                         -> exact_match
network status                    -> exact_match
connected please                  -> command_prefix
Are we connected?                 -> example_match
```

Messages that do not match a rule may still be classified locally from the
intent name, description, aliases, examples and parameters.

## Main Files

| File | Responsibility |
| --- | --- |
| `ai/intents/models.py` | Intent, result and request data models |
| `ai/intents/registry.py` | Intent registration, lookup and validation |
| `ai/intents/discovery.py` | Builds a registry from loaded command modules |
| `ai/intents/router.py` | Detection strategies and confidence handling |
| `ai/intents/factory.py` | Configures the default strategy order |
| `ai/intents/local_classifier.py` | Local model prompt and response validation |
| `ai/intents/dispatcher.py` | Runs the selected command module |
| `utils/on_command.py` | Connects routing, dispatch and LLM fallback |

For the command module contract and a copyable example, see
[Command Modules](command-modules.md).

## Tests

Run the intent tests with:

```powershell
python -m unittest discover -s tests -p "test_intent_*.py" -v
```

Run the complete test suite with:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```
