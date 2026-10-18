import asyncio
import json

from investigator_agent import prompts
from investigator_agent.config import AgentConfig
from investigator_agent.evaluation.cases import CASES_BY_ID
from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.evaluation.scoring import score
from investigator_agent.factory import make_investigator
from investigator_agent.llm_only import LLMOnly
from investigator_agent.testing import ScriptedLLM, call, respond
from investigator_agent.tools import ToolRegistry

ROLE = "llm-only"
GUESS = {
    "summary": "Вероятно, остановлен User Service: Order Service не получает ответа.",
    "category": "component_down",
    "component": "user-service",
    "recommendation": "Проверить состояние контейнера user-service.",
    "confidence": 0.5,
}


def investigator(llm):
    registry = ToolRegistry(ReplayWorld.named("INC-004").backend())
    return make_investigator("llm_only", llm, registry, AgentConfig(llm_model="test"))


def test_one_answer_without_diagnostic_tools():
    llm = ScriptedLLM({ROLE: [respond(call("submit_report", **GUESS))]})
    agent = investigator(llm)
    assert isinstance(agent, LLMOnly)
    case = CASES_BY_ID["INC-001"]
    result = asyncio.run(agent.investigate(case.symptom))
    assert result.system == "llm_only" and result.status == "completed"
    assert llm.calls[0]["tools"] == ["submit_report"]
    assert result.llm_calls == 1 and result.tool_calls == []
    item = score(case.id, 1, result, case.expected)
    assert item.correct and not item.grounded


def test_prompt_has_symptom_and_system_but_no_tool_notes_or_case():
    llm = ScriptedLLM({ROLE: [respond(call("submit_report", **GUESS))]})
    case = CASES_BY_ID["INC-004"]
    asyncio.run(investigator(llm).investigate(case.symptom))
    system, user = (m["content"] for m in llm.calls[0]["messages"])
    assert prompts.SYSTEM_TOPOLOGY in system and prompts.REPORT_RULES in system
    assert prompts.TOOL_NOTES not in system and prompts.METHOD not in system
    assert case.symptom in user
    sent = json.dumps(llm.calls[0]["messages"], ensure_ascii=False)
    assert "INC-" not in sent and "9999" not in sent


def test_invalid_report_is_returned_to_the_model():
    llm = ScriptedLLM(
        {
            ROLE: [
                respond(call("submit_report", category="broken")),
                respond(call("submit_report", **GUESS)),
            ]
        }
    )
    result = asyncio.run(investigator(llm).investigate("symptom"))
    assert result.status == "completed" and result.llm_calls == 2
    assert "Invalid submit_report arguments" in llm.calls[1]["messages"][-1]["content"]


def test_llm_failure_gives_inconclusive_report():
    result = asyncio.run(investigator(ScriptedLLM({})).investigate("symptom"))
    assert result.status == "llm_error" and result.error
    assert result.report.category == "unknown" and result.report.confidence == 0
