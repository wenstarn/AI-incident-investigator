import asyncio
import json

from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.llm import LLMError
from investigator_agent.loop import run_tool_loop
from investigator_agent.report import IncidentReport
from investigator_agent.single_agent import SUBMIT_REPORT
from investigator_agent.testing import ScriptedLLM, call, respond
from investigator_agent.tools import ToolRegistry

ROLE = "loop-test"
REPORT = {
    "summary": "Всё в порядке",
    "category": "no_incident",
    "component": "none",
    "evidence": [{"tool": "check_redis", "observation": "PING ok"}],
    "confidence": 0.9,
}


def run(llm, max_steps=5, registry="healthy"):
    if registry == "healthy":
        registry = ToolRegistry(ReplayWorld.named("healthy").backend())
    return asyncio.run(
        run_tool_loop(
            llm=llm,
            agent_name="tester",
            system_prompt=f"ROLE: {ROLE}\nTest prompt",
            user_prompt="symptom",
            registry=registry,
            final_tool=SUBMIT_REPORT,
            max_steps=max_steps,
        )
    )


def tool_messages(outcome):
    return [m for m in outcome.messages if m["role"] == "tool"]


def test_tool_call_then_final_report():
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("check_redis", call_id="a")),
                respond(call("submit_report", call_id="b", **REPORT)),
            ]
        }
    )
    outcome = run(llm)
    assert outcome.status == "completed"
    assert isinstance(outcome.final, IncidentReport)
    assert outcome.final.category == "no_incident"
    assert [record.tool for record in outcome.tool_calls] == ["check_redis"]
    assert outcome.llm_calls == 2 and outcome.usage.total_tokens == 240
    assert [m["tool_call_id"] for m in tool_messages(outcome)] == ["a", "b"]
    assert "submit_report" in llm.calls[0]["tools"]


def test_parallel_tool_calls_in_one_response():
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("check_redis"), call("check_postgres")),
                respond(call("submit_report", **REPORT)),
            ]
        }
    )
    outcome = run(llm)
    assert [record.tool for record in outcome.tool_calls] == [
        "check_redis",
        "check_postgres",
    ]
    assert len(tool_messages(outcome)) == 3


def test_invalid_final_is_returned_to_model_for_correction():
    bad = {**REPORT, "category": "it-is-broken"}
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("submit_report", **bad)),
                respond(call("submit_report", **REPORT)),
            ]
        }
    )
    outcome = run(llm)
    assert outcome.status == "completed" and outcome.llm_calls == 2
    assert "Invalid submit_report arguments" in tool_messages(outcome)[0]["content"]


def test_text_reply_gets_reminder_and_json_text_is_accepted():
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(content="Let me think about it."),
                respond(content=f"Final answer: {json.dumps(REPORT)}"),
            ]
        }
    )
    outcome = run(llm)
    assert outcome.status == "completed"
    assert any(
        m["role"] == "user" and "submit_report" in m["content"]
        for m in outcome.messages[2:]
    )


def test_step_limit_forces_final_tool():
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("check_redis")),
                respond(call("check_redis")),
                respond(call("submit_report", **REPORT)),
            ]
        }
    )
    outcome = run(llm, max_steps=2)
    assert outcome.status == "step_limit" and outcome.final is not None
    forced = llm.calls[-1]
    assert forced["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_report"},
    }
    assert forced["tools"] == ["submit_report"]


def test_step_limit_without_final():
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("check_redis")),
                respond(content="no"),
                respond(content="no"),
            ]
        }
    )
    outcome = run(llm, max_steps=1)
    assert outcome.status == "step_limit" and outcome.final is None
    assert outcome.llm_calls == 3


def test_forced_final_rejects_other_tools_and_retries():
    # Some servers ignore tool_choice and the restricted tool list.
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("check_redis")),
                respond(call("check_postgres", call_id="x")),
                respond(call("submit_report", **REPORT)),
            ]
        }
    )
    outcome = run(llm, max_steps=1)
    assert outcome.status == "step_limit" and outcome.final is not None
    rejected = next(m for m in outcome.messages if m.get("tool_call_id") == "x")
    assert "Only submit_report is allowed" in rejected["content"]
    assert [record.tool for record in outcome.tool_calls] == ["check_redis"]


def test_duplicate_calls_reuse_result_without_backend_call():
    calls = []
    backend = dict(ReplayWorld.named("healthy").backend())
    original = backend["check_redis"]

    async def counted():
        calls.append(1)
        return await original()

    backend["check_redis"] = counted
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("check_redis")),
                respond(call("check_redis", call_id="dup")),
                respond(call("submit_report", **REPORT)),
            ]
        }
    )
    outcome = run(llm, registry=ToolRegistry(backend))
    assert len(calls) == 1
    assert [record.duplicate for record in outcome.tool_calls] == [False, True]
    duplicate = next(m for m in outcome.messages if m.get("tool_call_id") == "dup")
    assert "identical call already made" in duplicate["content"]


def test_warning_before_step_limit():
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("check_redis")),
                respond(call("submit_report", **REPORT)),
            ]
        }
    )
    outcome = run(llm, max_steps=3)
    assert any(
        m["role"] == "user" and "Only 2 steps are left" in m["content"]
        for m in outcome.messages
    )


def test_llm_error_stops_loop():
    llm = ScriptedLLM({ROLE: [respond(call("check_redis")), LLMError("down")]})
    outcome = run(llm)
    assert outcome.status == "llm_error" and outcome.error == "down"
    assert len(outcome.tool_calls) == 1


def test_agent_without_tools_cannot_call_diagnostics():
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("check_redis")),
                respond(call("submit_report", **REPORT)),
            ]
        }
    )
    outcome = run(llm, registry=None)
    assert llm.calls[0]["tools"] == ["submit_report"]
    assert outcome.tool_calls[0].result["error"]["type"] == "tool_not_allowed"
    assert outcome.status == "completed"
