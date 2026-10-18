"""Progress events emitted while an investigation runs.

Agents report every step to an optional sink. The agent console streams these
events to the browser, and every InvestigationResult stores them so a finished
run can be replayed. Event types:

- investigation_started / investigation_finished: whole run (system level);
- agent_started / agent_finished: one role's tool loop;
- llm_response: what the model said and which tools it asked for;
- tool_result: result of a diagnostic tool call;
- final: an accepted final-tool payload (report, dispatch, findings, verdict);
- round: MAS review round with the tasks given to specialists;
- error: the language model failed.
"""

import time
from collections.abc import Callable
from typing import Any

EventSink = Callable[[dict[str, Any]], None]


class EventRecorder:
    """Stores events and forwards them to an optional downstream sink."""

    def __init__(self, forward: EventSink | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self._forward = forward
        self._started = time.monotonic()

    def __call__(self, event: dict[str, Any]) -> None:
        event = {"t": round(time.monotonic() - self._started, 3), **event}
        self.events.append(event)
        if self._forward is not None:
            self._forward(event)


def emit(sink: EventSink | None, event_type: str, **fields: Any) -> None:
    if sink is not None:
        sink({"type": event_type, **fields})
