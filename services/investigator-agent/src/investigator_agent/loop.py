"""Shared tool-calling loop used by the single agent and by every MAS role.

An agent finishes by calling its *final tool* (for example ``submit_report``).
The final tool's arguments are validated by a Pydantic model; validation errors
are returned to the model so it can correct itself.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from investigator_agent.events import EventSink, emit
from investigator_agent.llm import LLMClient, LLMError, LLMResponse, ToolCall, Usage
from investigator_agent.tools import (
    ToolCallRecord,
    ToolRegistry,
    function_schema,
    parse_arguments,
    validation_summary,
)

NO_TOOL_CALL_REMINDER = (
    "Respond only with tool calls. Use the diagnostic tools to gather evidence, "
    "then call {final} with your conclusion."
)
STEPS_WARNING_AT = 2
STEPS_WARNING = (
    "Only {remaining} steps are left. Stop exploring unless a check can change "
    "the conclusion, and call {final} soon."
)
DUPLICATE_NOTE = (
    "\nNOTE: identical call already made; this is the same result. "
    "Do not repeat calls - use new checks or submit your conclusion."
)
FORCED_FINAL_ATTEMPTS = 2
MAX_TASK_EVENT_CHARS = 3000
STEP_LIMIT_MESSAGE = (
    "The step budget is exhausted. Call {final} now with the best conclusion "
    "supported by the evidence collected so far."
)


@dataclass(frozen=True)
class FinalTool:
    name: str
    description: str
    model: type[BaseModel]

    def schema(self) -> dict[str, Any]:
        return function_schema(self.name, self.description, self.model)


@dataclass
class LoopOutcome:
    final: BaseModel | None
    status: Literal["completed", "step_limit", "llm_error"]
    error: str | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    llm_calls: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": response.content}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ]
    return message


def _json_from_text(text: str | None) -> dict[str, Any] | None:
    """Accept a final answer written as JSON text by models that skip tools."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _validate_final(
    final_tool: FinalTool, raw: str | dict[str, Any] | None
) -> tuple[BaseModel | None, str]:
    try:
        arguments = parse_arguments(raw)
        # Some models wrap the payload in an extra object named after the tool.
        if set(arguments) == {final_tool.name} and isinstance(
            arguments[final_tool.name], dict
        ):
            arguments = arguments[final_tool.name]
        return final_tool.model.model_validate(arguments), "accepted"
    except ValidationError as error:
        return None, f"Invalid {final_tool.name} arguments: {validation_summary(error)}"
    except ValueError:
        return None, f"Invalid {final_tool.name} arguments: not a JSON object"


async def run_tool_loop(
    *,
    llm: LLMClient,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    registry: ToolRegistry | None,
    final_tool: FinalTool,
    max_steps: int,
    on_event: EventSink | None = None,
) -> LoopOutcome:
    outcome = await _run_tool_loop(
        llm=llm,
        agent_name=agent_name,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        registry=registry,
        final_tool=final_tool,
        max_steps=max_steps,
        on_event=on_event,
    )
    if outcome.final is not None:
        emit(
            on_event,
            "final",
            agent=agent_name,
            tool=final_tool.name,
            payload=outcome.final.model_dump(mode="json"),
        )
    if outcome.error:
        emit(on_event, "error", agent=agent_name, message=outcome.error)
    emit(on_event, "agent_finished", agent=agent_name, status=outcome.status)
    return outcome


async def _run_tool_loop(
    *,
    llm: LLMClient,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    registry: ToolRegistry | None,
    final_tool: FinalTool,
    max_steps: int,
    on_event: EventSink | None,
) -> LoopOutcome:
    # Synthesis and critic inputs embed the whole observation log; the event
    # only needs the beginning (symptom and task).
    emit(
        on_event,
        "agent_started",
        agent=agent_name,
        task=user_prompt[:MAX_TASK_EVENT_CHARS],
    )
    outcome = LoopOutcome(final=None, status="step_limit")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    outcome.messages = messages
    registry = registry or ToolRegistry({}, names=())
    tools = registry.schemas() + [final_tool.schema()]

    async def ask(
        tool_list: list[dict[str, Any]], tool_choice: str | dict[str, Any] = "auto"
    ) -> LLMResponse | None:
        try:
            response = await llm.complete(messages, tool_list, tool_choice)
        except LLMError as error:
            outcome.status = "llm_error"
            outcome.error = str(error)
            return None
        outcome.llm_calls += 1
        outcome.usage.add(response.usage)
        messages.append(_assistant_message(response))
        emit(
            on_event,
            "llm_response",
            agent=agent_name,
            content=response.content or "",
            tool_calls=[
                {"name": call.name, "arguments": call.arguments}
                for call in response.tool_calls
            ],
            tokens=response.usage.total_tokens,
        )
        return response

    seen: dict[str, tuple[str, ToolCallRecord]] = {}

    async def run_call(call: ToolCall) -> str:
        try:
            key = call.name + json.dumps(
                parse_arguments(call.arguments), sort_keys=True
            )
        except ValueError:
            key = None
        if key in seen:
            # Small models tend to loop; repeat the answer instead of re-probing.
            content, record = seen[key]
            record = record.model_copy(update={"duplicate": True, "duration_ms": 0.0})
            outcome.tool_calls.append(record)
            emit(on_event, "tool_result", **record.model_dump(mode="json"))
            return content + DUPLICATE_NOTE
        content, record = await registry.execute(call.name, call.arguments, agent_name)
        outcome.tool_calls.append(record)
        emit(on_event, "tool_result", **record.model_dump(mode="json"))
        if key is not None and record.status != "error":
            seen[key] = (content, record)
        return content

    for step in range(max_steps):
        response = await ask(tools)
        if response is None:
            return outcome
        if not response.tool_calls:
            text_final = _json_from_text(response.content)
            if text_final is not None:
                final, _ = _validate_final(final_tool, text_final)
                if final is not None:
                    outcome.final = final
                    outcome.status = "completed"
                    return outcome
            messages.append(
                {
                    "role": "user",
                    "content": NO_TOOL_CALL_REMINDER.format(final=final_tool.name),
                }
            )
            continue
        diagnostic_calls = [c for c in response.tool_calls if c.name != final_tool.name]
        results = dict(
            zip(
                [c.id for c in diagnostic_calls],
                await asyncio.gather(*(run_call(c) for c in diagnostic_calls)),
                strict=True,
            )
        )
        final = None
        for call in response.tool_calls:
            if call.name == final_tool.name:
                candidate, content = _validate_final(final_tool, call.arguments)
                final = final or candidate
            else:
                content = results[call.id]
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": content}
            )
        if final is not None:
            outcome.final = final
            outcome.status = "completed"
            return outcome
        remaining = max_steps - step - 1
        if remaining == STEPS_WARNING_AT:
            messages.append(
                {
                    "role": "user",
                    "content": STEPS_WARNING.format(
                        remaining=remaining, final=final_tool.name
                    ),
                }
            )

    # Step budget exhausted: insist on the final tool. Some servers ignore
    # tool_choice, so other calls are answered with an error and retried once.
    messages.append(
        {"role": "user", "content": STEP_LIMIT_MESSAGE.format(final=final_tool.name)}
    )
    forced = {"type": "function", "function": {"name": final_tool.name}}
    for _ in range(FORCED_FINAL_ATTEMPTS):
        response = await ask([final_tool.schema()], forced)
        if response is None:
            return outcome
        for call in response.tool_calls:
            if call.name == final_tool.name:
                candidate, content = _validate_final(final_tool, call.arguments)
                outcome.final = outcome.final or candidate
            else:
                content = json.dumps(
                    {
                        "status": "error",
                        "error": {
                            "type": "tool_not_allowed",
                            "message": f"Only {final_tool.name} is allowed now.",
                        },
                    }
                )
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": content}
            )
        if outcome.final is None:
            candidate, _ = _validate_final(
                final_tool, _json_from_text(response.content)
            )
            outcome.final = candidate
        if outcome.final is not None:
            break
    outcome.status = "step_limit"
    return outcome
