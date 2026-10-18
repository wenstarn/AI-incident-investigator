"""Turn a saved investigation into a replayable event sequence.

Results saved after progress events were introduced already carry ``events``.
Older results only have per-agent transcripts; for them the sequence is
reconstructed from the messages with synthetic timing.
"""

import json
from typing import Any

FINAL_TOOLS = frozenset(
    {"submit_report", "dispatch", "submit_findings", "submit_verdict"}
)
SYNTHETIC_STEP_S = 0.8


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def finished_summary(result: dict[str, Any]) -> dict[str, Any]:
    usage = result.get("usage") or {}
    return {
        "system": result.get("system"),
        "status": result.get("status"),
        "error": result.get("error"),
        "report": result.get("report"),
        "tool_calls": len(result.get("tool_calls") or []),
        "llm_calls": result.get("llm_calls", 0),
        "tokens": usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
        "duration_s": result.get("duration_s"),
    }


def reconstruct_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "type": "investigation_started",
            "system": result.get("system"),
            "symptom": result.get("symptom"),
        }
    ]
    for entry in result.get("transcript") or []:
        agent = entry.get("agent", "agent")
        messages = entry.get("messages") or []
        task = messages[1].get("content", "") if len(messages) > 1 else ""
        events.append({"type": "agent_started", "agent": agent, "task": task})
        calls: dict[str, tuple[str, str]] = {}
        for message in messages[2:]:
            if message.get("role") == "assistant":
                tool_calls = [
                    {
                        "name": call["function"]["name"],
                        "arguments": call["function"]["arguments"],
                    }
                    for call in message.get("tool_calls") or []
                ]
                for raw, call in zip(
                    message.get("tool_calls") or [], tool_calls, strict=True
                ):
                    calls[raw.get("id", "")] = (call["name"], call["arguments"])
                events.append(
                    {
                        "type": "llm_response",
                        "agent": agent,
                        "content": message.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )
            elif message.get("role") == "tool":
                name, arguments = calls.get(
                    message.get("tool_call_id", ""), ("?", "{}")
                )
                content = message.get("content", "")
                if name in FINAL_TOOLS:
                    if content == "accepted":
                        events.append(
                            {
                                "type": "final",
                                "agent": agent,
                                "tool": name,
                                "payload": _loads(arguments) or {},
                            }
                        )
                    continue
                payload = _loads(content.split("\nNOTE:")[0]) or {}
                events.append(
                    {
                        "type": "tool_result",
                        "agent": agent,
                        "tool": name,
                        "arguments": _loads(arguments) or {},
                        "status": payload.get("status", "error"),
                        "result": payload,
                        "duplicate": "\nNOTE:" in content,
                    }
                )
        events.append(
            {
                "type": "agent_finished",
                "agent": agent,
                "status": entry.get("status", result.get("status")),
            }
        )
    events.append({"type": "investigation_finished", **finished_summary(result)})
    return [
        {"t": round(index * SYNTHETIC_STEP_S, 3), **event}
        for index, event in enumerate(events)
    ]


def events_for_playback(result: dict[str, Any]) -> list[dict[str, Any]]:
    return result.get("events") or reconstruct_events(result)
