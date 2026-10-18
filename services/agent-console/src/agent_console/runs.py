"""In-memory store of running investigations and their event streams."""

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

MAX_RUNS = 20
HEARTBEAT_SECONDS = 15


class Run:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.task: asyncio.Task | None = None
        self._started = time.monotonic()
        self._changed = asyncio.Event()

    def push(self, event: dict[str, Any]) -> None:
        event = {"t": round(time.monotonic() - self._started, 3), **event}
        self.events.append(event)
        self._notify()

    def finish(self) -> None:
        self.done = True
        self._notify()

    def _notify(self) -> None:
        # Wake every waiter of the current generation, then start a new one.
        self._changed.set()
        self._changed = asyncio.Event()

    async def follow(self) -> AsyncIterator[dict[str, Any] | None]:
        """Yield all events from the start; None is a heartbeat."""
        index = 0
        while True:
            while index < len(self.events):
                yield self.events[index]
                index += 1
            if self.done:
                return
            changed = self._changed
            try:
                await asyncio.wait_for(changed.wait(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield None


class RunStore:
    def __init__(self) -> None:
        self._runs: OrderedDict[str, Run] = OrderedDict()

    def create(self) -> Run:
        run = Run()
        self._runs[run.id] = run
        while len(self._runs) > MAX_RUNS:
            self._runs.popitem(last=False)
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)


def sse(event: dict[str, Any] | None) -> str:
    if event is None:
        return ": heartbeat\n\n"
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


SSE_END = "event: end\ndata: {}\n\n"
