# Command Modules

CODA discovers commands from Python files in the `commands` directory. A
command owns both the metadata used for intent detection and the function used
for execution, so adding one does not require editing a central catalogue.

## Minimal Command

Create `commands/greet.py`:

```python
from ai.intents import Intent, IntentRequest


INTENT = Intent(
    name="greet",
    description="Greet the user.",
    aliases=("hello", "say hello"),
    examples=(
        "Hello there.",
        "Please greet me.",
    ),
)


def run(request: IntentRequest) -> bool:
    print("Hello!")
    return True
```

The filename must match the canonical intent name: an intent named `greet`
belongs in `greet.py`. Restart CODA after adding or removing a command. The
command cache is rebuilt automatically when the files in `commands` change.

Use `aliases` for alternate command triggers that may prefix additional
content. Use `examples` for complete natural-language phrasings that should
deterministically select the intent without calling the local classifier.

## Module Contract

Every command module must expose:

- `INTENT`, containing an `Intent` instance with a unique name and aliases.
- `run(request: IntentRequest) -> bool`, containing the command behaviour.

Return `True` when the request was handled successfully and `False` when the
command could not complete it. CODA uses that result when reporting whether a
request was handled; a failed command does not fall through to general LLM
chat.

The structured request provides:

- `request.intent`: the matched `Intent` metadata.
- `request.message`: the original command message.
- `request.confidence`: the router's confidence score.
- `request.strategy`: the detection strategy that selected the intent.

## Parameters

Use `IntentParameter` to describe information that the command expects:

```python
from ai.intents import Intent, IntentParameter, IntentRequest


INTENT = Intent(
    name="weather",
    description="Report the weather for a location.",
    parameters=(
        IntentParameter(
            name="location",
            description="The place to report weather for.",
        ),
    ),
    aliases=("forecast",),
)
```

Parameters describe the intent to detection strategies. The command remains
responsible for extracting the values it needs from `request.message`.

## Validation

CODA validates command modules while configuring the intent router. Startup
fails with a clear error when:

- `INTENT` is missing or is not an `Intent` instance.
- The filename and canonical intent name do not match.
- `run` is missing or is not callable.
- An intent name or alias conflicts with another command.

Keeping validation at startup prevents an invalid command from failing only
after a user tries to run it.
