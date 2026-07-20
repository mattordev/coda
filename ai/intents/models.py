from dataclasses import dataclass

@dataclass(frozen=True)
class IntentParameter:
    name: str
    description: str
    required: bool = True
    
@dataclass(frozen=True)
class Intent:
    name: str
    description: str
    parameters: tuple[IntentParameter, ...] = ()
    aliases: tuple[str, ...] = ()
    