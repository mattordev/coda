from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ai.intents.models import IntentRequest
    from ai.tools.models import ToolResult


class ToolExecutor(Protocol):
    def execute(self, request: IntentRequest) -> ToolResult:
        """Execute a capability and return its normalized outcome."""