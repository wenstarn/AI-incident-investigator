import asyncio
import json
import re

from investigator_agent import prompts
from investigator_agent.config import AgentConfig
from investigator_agent.evaluation.cases import CASES_BY_ID
from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.evaluation.scoring import score
from investigator_agent.single_agent import SingleAgent
from investigator_agent.testing import ScriptedLLM, call, respond
from investigator_agent.tools import ToolRegistry

ROLE = "single-investigator"
WRONG_PORT_REPORT = {
    "summary": "Order Service обращается к User Service на неверный порт 9999.",
    "category": "dependency_misconfiguration",
    "component": "order-service",
    "dependency": "user-service",
    "evidence": [
        {"tool": "check_http_endpoint", "observation": "/health User Service 200"},
        {"tool": "check_service_dependency", "observation": "connection_refused"},
        {"tool": "get_dependency_config", "observation": "port=9999"},
    ],
    "ruled_out": ["User Service не остановлен: прямой /health отвечает 200"],
    "recommendation": "Вернуть порт 8001 в конфигурации Order Service.",
    "confidence": 0.9,
}


def agent(llm, world="INC-004", **config):
    registry = ToolRegistry(ReplayWorld.named(world).backend())
    return SingleAgent(llm, registry, AgentConfig(llm_model="test", **config))


def test_end_to_end_investigation_in_replay_world():
    def submit_after_reading(messages):
        observed = [m["content"] for m in messages if m["role"] == "tool"]
        assert any('"port": 9999' in content for content in observed)
        return respond(call("submit_report", **WRONG_PORT_REPORT))

    llm = ScriptedLLM(
        {
            ROLE: [
                respond(
                    call("check_http_endpoint", service="user-service", path="/health"),
                    call(
                        "check_service_dependency",
                        source_service="order-service",
                        dependency="user-service",
                    ),
                ),
                respond(
                    call(
                        "get_dependency_config",
                        service="order-service",
                        dependency="user-service",
                    )
                ),
                submit_after_reading,
            ]
        }
    )
    case = CASES_BY_ID["INC-004"]
    result = asyncio.run(agent(llm).investigate(case.symptom))
    assert result.system == "single" and result.status == "completed"
    assert len(result.tool_calls) == 3 and result.llm_calls == 3
    assert result.transcript[0]["messages"][1]["content"].startswith("Symptom report")
    item = score(case.id, 1, result, case.expected)
    assert item.correct and item.grounded and item.tool_calls == 3


def test_symptom_is_the_only_case_information_given():
    llm = ScriptedLLM({ROLE: [respond(call("submit_report", **WRONG_PORT_REPORT))]})
    case = CASES_BY_ID["INC-004"]
    asyncio.run(agent(llm).investigate(case.symptom))
    sent = json.dumps(llm.calls[0]["messages"], ensure_ascii=False)
    assert case.symptom in sent
    assert "INC-" not in sent and "9999" not in sent


def test_no_report_gives_inconclusive_result():
    llm = ScriptedLLM(
        {ROLE: [respond(call("check_redis"))] + [respond(content="?")] * 2}
    )
    result = asyncio.run(agent(llm, max_steps=1).investigate("symptom"))
    assert result.status == "step_limit"
    assert result.report.category == "unknown" and result.report.confidence == 0


def test_llm_failure_is_reported():
    llm = ScriptedLLM({})
    result = asyncio.run(agent(llm).investigate("symptom"))
    assert result.status == "llm_error" and result.error
    assert result.report.category == "unknown"


def test_prompts_do_not_leak_scenarios_or_controller():
    texts = [
        prompts.SINGLE_AGENT_PROMPT,
        prompts.COORDINATOR_TRIAGE_PROMPT,
        prompts.COORDINATOR_SYNTHESIS_PROMPT,
        prompts.CRITIC_PROMPT,
        *(prompts.specialist_prompt(name) for name in prompts.SPECIALIST_PROMPTS),
    ]
    for text in texts:
        assert text.startswith("ROLE: ")
        # One language rule for every role, so all visible text is Russian.
        assert prompts.LANGUAGE_RULE in text
        assert text.count("in Russian") == 1
        lowered = text.lower()
        assert "inc-" not in lowered
        assert "controller" not in lowered and "8003" not in lowered
        assert not re.search(r"\bfault", lowered)
        assert "9999" not in lowered
