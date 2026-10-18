"""Deterministic stand-in for an LLM, used by unit tests and offline demos.

Responses are queued per role. The role is read from the ``ROLE: ...`` line
that every system prompt starts with, so concurrent MAS roles stay independent.
"""

import itertools
import json
from collections.abc import Callable
from typing import Any

from investigator_agent.llm import LLMError, LLMResponse, ToolCall, Usage

_call_ids = itertools.count(1)

Scripted = LLMResponse | Exception | Callable[[list[dict[str, Any]]], LLMResponse]


def call(name: str, call_id: str | None = None, **arguments: Any) -> ToolCall:
    return ToolCall(
        id=call_id or f"call_{next(_call_ids)}",
        name=name,
        arguments=json.dumps(arguments, ensure_ascii=False),
    )


def respond(
    *calls: ToolCall, content: str | None = None, tokens: tuple[int, int] = (100, 20)
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=list(calls),
        usage=Usage(prompt_tokens=tokens[0], completion_tokens=tokens[1]),
    )


def role_of(messages: list[dict[str, Any]]) -> str:
    system = messages[0]["content"] if messages else ""
    first_line = system.splitlines()[0] if system else ""
    return first_line.removeprefix("ROLE:").strip()


class ScriptedLLM:
    def __init__(self, scripts: dict[str, list[Scripted]]) -> None:
        self._scripts = {role: list(items) for role, items in scripts.items()}
        self.calls: list[dict[str, Any]] = []

    def remaining(self, role: str) -> int:
        return len(self._scripts.get(role, []))

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> LLMResponse:
        role = role_of(messages)
        self.calls.append(
            {
                "role": role,
                "tools": [tool["function"]["name"] for tool in tools],
                "tool_choice": tool_choice,
                "messages": list(messages),
            }
        )
        queue = self._scripts.get(role)
        if not queue:
            raise LLMError(f"No scripted response left for role {role!r}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(messages)
        return item
