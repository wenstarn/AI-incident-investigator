import asyncio
import json

from investigator_agent.config import AgentConfig
from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.mas.system import MultiAgentSystem
from investigator_agent.playback import events_for_playback, reconstruct_events
from investigator_agent.single_agent import SingleAgent
from investigator_agent.testing import ScriptedLLM, call, respond
from investigator_agent.tools import ToolRegistry

REPORT = {
    "summary": "Redis остановлен",
    "category": "component_down",
    "component": "redis",
    "evidence": [{"tool": "check_redis", "observation": "refused"}],
    "confidence": 0.8,
}


def registry(world="INC-002"):
    return ToolRegistry(ReplayWorld.named(world).backend())


def single_llm():
    return ScriptedLLM(
        {
            "single-investigator": [
                respond(call("check_redis"), content="Проверю Redis напрямую"),
                respond(call("check_redis")),
                respond(call("submit_report", **REPORT)),
            ]
        }
    )


def test_single_agent_streams_events_in_order():
    seen = []
    agent = SingleAgent(single_llm(), registry(), AgentConfig(llm_model="t"))
    result = asyncio.run(agent.investigate("symptom", on_event=seen.append))
    types = [event["type"] for event in seen]
    assert types == [
        "investigation_started",
        "agent_started",
        "llm_response",
        "tool_result",
        "llm_response",
        "tool_result",
        "llm_response",
        "final",
        "agent_finished",
        "investigation_finished",
    ]
    assert seen[2]["content"] == "Проверю Redis напрямую"
    assert seen[2]["tool_calls"][0]["name"] == "check_redis"
    assert seen[3]["status"] == "degraded" and seen[3]["agent"] == "single-investigator"
    assert seen[5]["duplicate"] is True
    assert seen[7]["payload"]["component"] == "redis"
    assert seen[-1]["report"]["category"] == "component_down"
    assert seen[-1]["tool_calls"] == 2
    assert all(event["t"] >= 0 for event in seen)
    assert result.events == seen
    json.dumps(result.model_dump(mode="json"))


def test_mas_streams_events_from_all_roles():
    findings = respond(
        call(
            "submit_findings",
            observations=[{"tool": "check_redis", "observation": "refused"}],
            interpretation="Redis недоступен",
            confidence=0.8,
        )
    )
    llm = ScriptedLLM(
        {
            "coordinator-triage": [
                respond(
                    call(
                        "dispatch",
                        hypotheses=["Redis остановлен"],
                        tasks=[{"specialist": "datastore", "task": "PING"}],
                    )
                )
            ],
            "specialist-datastore": [respond(call("check_redis")), findings],
            "coordinator-synthesis": [respond(call("submit_report", **REPORT))],
            "critic": [respond(call("submit_verdict", approved=True))],
        }
    )
    seen = []
    system = MultiAgentSystem(llm, registry(), AgentConfig(llm_model="t"))
    result = asyncio.run(system.investigate("symptom", on_event=seen.append))
    started = [e["agent"] for e in seen if e["type"] == "agent_started"]
    assert started == [
        "coordinator-triage",
        "specialist-datastore",
        "coordinator-synthesis",
        "critic",
    ]
    finals = {e["tool"]: e["payload"] for e in seen if e["type"] == "final"}
    assert set(finals) == {
        "dispatch",
        "submit_findings",
        "submit_report",
        "submit_verdict",
    }
    assert finals["submit_verdict"]["approved"] is True
    rounds = [e for e in seen if e["type"] == "round"]
    assert rounds == [
        {
            "t": rounds[0]["t"],
            "type": "round",
            "round": 1,
            "tasks": [{"specialist": "datastore", "task": "PING"}],
        }
    ]
    assert seen[0]["type"] == "investigation_started" and seen[0]["system"] == "mas"
    assert seen[-1]["type"] == "investigation_finished"
    assert result.events == seen


def test_llm_error_is_streamed():
    seen = []
    agent = SingleAgent(ScriptedLLM({}), registry(), AgentConfig(llm_model="t"))
    asyncio.run(agent.investigate("symptom", on_event=seen.append))
    errors = [e for e in seen if e["type"] == "error"]
    assert errors and "No scripted response" in errors[0]["message"]
    assert seen[-1]["status"] == "llm_error"


def test_playback_uses_recorded_events_or_reconstructs_them():
    agent = SingleAgent(single_llm(), registry(), AgentConfig(llm_model="t"))
    result = asyncio.run(agent.investigate("symptom")).model_dump(mode="json")
    assert events_for_playback(result) == result["events"]

    legacy = {**result, "events": []}
    rebuilt = events_for_playback(legacy)
    assert [e["type"] for e in rebuilt] == [
        "investigation_started",
        "agent_started",
        "llm_response",
        "tool_result",
        "llm_response",
        "tool_result",
        "llm_response",
        "final",
        "agent_finished",
        "investigation_finished",
    ]
    assert rebuilt[3]["tool"] == "check_redis" and rebuilt[3]["status"] == "degraded"
    assert rebuilt[5]["duplicate"] is True
    assert rebuilt[7]["payload"]["component"] == "redis"
    assert rebuilt[-1]["tool_calls"] == 2
    assert [e["t"] for e in rebuilt] == sorted(e["t"] for e in rebuilt)


def test_reconstruct_tolerates_minimal_result():
    events = reconstruct_events({"system": "single", "symptom": "s"})
    assert [e["type"] for e in events] == [
        "investigation_started",
        "investigation_finished",
    ]


def test_agent_started_event_is_trimmed():
    from investigator_agent.loop import MAX_TASK_EVENT_CHARS

    seen = []
    agent = SingleAgent(single_llm(), registry(), AgentConfig(llm_model="t"))
    asyncio.run(agent.investigate("x" * 10_000, on_event=seen.append))
    started = next(e for e in seen if e["type"] == "agent_started")
    assert len(started["task"]) == MAX_TASK_EVENT_CHARS
    assert started["task"].startswith("Symptom report:")
