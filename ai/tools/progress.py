"""Host-owned tool lifecycle feedback, without arguments or remote text."""

from dataclasses import dataclass

from ai.request_context import current_request


@dataclass(frozen=True)
class ToolFeedback:
    stage: str
    tool: str
    count: int | None = None


def report_tool(stage, tool, *, result=None):
    """Feedback is optional and must never change execution or its result."""
    context = current_request()
    if (context is None or context.progress is None or not isinstance(tool, str)
            or stage not in ('action', 'completed', 'progress')
            or (context.cancel_event is not None and context.cancel_event.is_set())):
        return
    count = None
    if stage == 'completed' and tool.rsplit('.', 1)[-1] == 'search_web':
        data = result.data if result is not None else None
        payload = data.get('structuredContent', data) if isinstance(data, dict) else None
        entries = payload.get('results') if isinstance(payload, dict) and payload.get('kind') == 'web_search' else None
        if isinstance(entries, list):
            count = min(len(entries), 10)
    try:
        context.progress(ToolFeedback(stage, tool, count))
    except Exception:
        # A speech failure cannot turn a successful tool into an execution error.
        pass
