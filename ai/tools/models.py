from dataclasses import dataclass

@dataclass(frozen=True)
class ToolError:
    """A normalized failure from executing a capability.

    `code` lets CODA respond programmatically, while message explains the error to the user.
    """

    code: str
    message: str

    def __post_init__(self) -> None:
        """Reject empty code or message to avoid errors that cannot be explained."""
        if not isinstance(self.code, str) or not isinstance(self.message, str):
            raise ValueError("Error code and message must be strings.")

        if not self.code.strip() or not self.message.strip():
            raise ValueError(
                "An error cannot be empty."
            )

@dataclass(frozen=True)
class ToolResult:
    """
    The outcome of executing a native command or external tool.

    Returns outcome (true/false) and can either return outcome data or empty and the error itself.
    """

    success: bool
    data: object | None = None
    error: ToolError | None = None

    def __post_init__(self) -> None:
        """Validate the result envelope and reject contradictory outcomes."""
        if not isinstance(self.success, bool):
            raise ValueError("Tool result success must be a boolean.")

        if self.error is not None and not isinstance(self.error, ToolError):
            raise ValueError("Tool result error must be a ToolError or None.")

        if self.success and self.error is not None:
            raise ValueError(
                "A successful tool result cannot also contain an error."
            )
