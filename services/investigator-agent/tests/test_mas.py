import asyncio

from diagnostic_tools.models import DiagnosticResult
from investigator_agent.config import AgentConfig
from investigator_agent.evaluation.cases import CASES_BY_ID
from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.evaluation.scoring import score
from investigator_agent.llm import LLMError
from investigator_agent.mas.models import SPECIALIST_TOOLS
from investigator_agent.mas.system import MultiAgentSystem
from investigator_agent.testing import ScriptedLLM, call, respond
from investigator_agent.tools import TOOL_SPECS, ToolRegistry

REDIS_PORT_REPORT = {
    "summary": "Order Service использует неверный порт Redis 9999.",
    "category": "dependency_misconfiguration",
    "component": "order-service",
    "dependency": "redis",
    "evidence": [
        {"tool": "check_redis", "observation": "Прямой PING успешен"},
        {"tool": "check_service_dependency", "observation": "connection_refused"},
        {"tool": "get_dependency_config", "observation": "port=9999"},
    ],
    "ruled_out": ["Redis не остановлен: прямой PING проходит"],
    "recommendation": "Вернуть порт 6379.",
    "confidence": 0.85,
}
DOWN_REPORT = {
    **REDIS_PORT_REPORT,
    "summary": "Redis недоступен",
    "category": "component_down",
    "component": "redis",
    "dependency": "none",
    "evidence": [
        {"tool": "check_service_dependency", "observation": "connection_refused"}
    ],
}


def findings(text="наблюдения"):
    return respond(
        call(
            "submit_findings",
            observations=[{"tool": "check_redis", "observation": text}],
            interpretation=text,
            confidence=0.7,
        )
    )


def dispatch(*tasks):
    return respond(
        call(
            "dispatch",
            hypotheses=["Redis остановлен", "Неверный адрес Redis"],
            tasks=[{"specialist": name, "task": text} for name, text in tasks],
        )
    )


def verdict(approved, checks=()):
    return respond(
        call(
            "submit_verdict",
            approved=approved,
            issues=[] if approved else ["Не исключена ошибка конфигурации"],
            requested_checks=[{"specialist": n, "task": t} for n, t in checks],
        )
    )


def system(llm, world="INC-006", backend=None, **config):
    registry = ToolRegistry(backend or ReplayWorld.named(world).backend())
    return MultiAgentSystem(llm, registry, AgentConfig(llm_model="test", **config))


def test_full_flow_approved_on_first_review():
    llm = ScriptedLLM(
        {
            "coordinator-triage": [
                respond(call("get_service_status", service="redis")),
                dispatch(
                    ("connectivity", "Проверь Redis из Order Service и конфиг"),
                    ("datastore", "Проверь Redis напрямую"),
                ),
            ],
            "specialist-connectivity": [
                respond(
                    call(
                        "check_service_dependency",
                        source_service="order-service",
                        dependency="redis",
                    ),
                    call(
                        "get_dependency_config",
                        service="order-service",
                        dependency="redis",
                    ),
                ),
                findings("Из Order Service отказ, порт 9999"),
            ],
            "specialist-datastore": [respond(call("check_redis")), findings("PING ok")],
            "coordinator-synthesis": [
                respond(call("submit_report", **REDIS_PORT_REPORT))
            ],
            "critic": [verdict(True)],
        }
    )
    case = CASES_BY_ID["INC-006"]
    result = asyncio.run(system(llm).investigate(case.symptom))
    assert result.system == "mas" and result.status == "completed"
    assert {record.agent for record in result.tool_calls} == {
        "coordinator-triage",
        "specialist-connectivity",
        "specialist-datastore",
    }
    assert len(result.details["rounds"]) == 1
    assert result.details["rounds"][0]["verdict"]["approved"] is True
    assert result.llm_calls == 8  # triage 2 + specialists 2+2 + synthesis + critic
    item = score(case.id, 1, result, case.expected)
    assert item.correct and item.grounded
    synthesis_input = next(
        c for c in llm.calls if c["role"] == "coordinator-synthesis"
    )["messages"][1]["content"]
    assert '"port": 9999' in synthesis_input and "PING ok" in synthesis_input


def test_specialists_get_only_their_tools():
    llm = ScriptedLLM(
        {
            "coordinator-triage": [dispatch(("datastore", "Проверь всё"))],
            "specialist-datastore": [
                respond(
                    call(
                        "get_dependency_config",
                        service="order-service",
                        dependency="redis",
                    )
                ),
                findings(),
            ],
            "coordinator-synthesis": [respond(call("submit_report", **DOWN_REPORT))],
            "critic": [verdict(True)],
        }
    )
    result = asyncio.run(system(llm).investigate("symptom"))
    offered = next(c for c in llm.calls if c["role"] == "specialist-datastore")["tools"]
    assert offered == [*SPECIALIST_TOOLS["datastore"], "submit_findings"]
    triage_tools = next(c for c in llm.calls if c["role"] == "coordinator-triage")[
        "tools"
    ]
    assert triage_tools == ["get_service_status", "dispatch"]
    for role in ("coordinator-synthesis", "critic"):
        tools = next(c for c in llm.calls if c["role"] == role)["tools"]
        assert not set(tools) & set(TOOL_SPECS)
    rejected = result.tool_calls[0]
    assert rejected.tool == "get_dependency_config"
    assert rejected.result["error"]["type"] == "tool_not_allowed"


def test_specialist_tool_sets_cover_all_tools_without_overlap():
    tools = [name for names in SPECIALIST_TOOLS.values() for name in names]
    assert sorted(tools) == sorted(TOOL_SPECS)


def test_critic_rejection_triggers_second_round():
    llm = ScriptedLLM(
        {
            "coordinator-triage": [
                dispatch(("connectivity", "Проверь Redis из Order Service"))
            ],
            "specialist-connectivity": [
                respond(
                    call(
                        "check_service_dependency",
                        source_service="order-service",
                        dependency="redis",
                    )
                ),
                findings("отказ из Order Service"),
            ],
            "specialist-datastore": [respond(call("check_redis")), findings("PING ok")],
            "coordinator-synthesis": [
                respond(call("submit_report", **DOWN_REPORT)),
                respond(call("submit_report", **REDIS_PORT_REPORT)),
            ],
            "critic": [
                verdict(False, [("datastore", "Сделай прямой PING Redis")]),
                verdict(True),
            ],
        }
    )
    result = asyncio.run(system(llm).investigate("symptom"))
    assert result.report.category == "dependency_misconfiguration"
    assert len(result.details["rounds"]) == 2
    assert result.details["rounds"][1]["tasks"][0]["specialist"] == "datastore"
    second_synthesis = [c for c in llm.calls if c["role"] == "coordinator-synthesis"][1]
    assert (
        "Не исключена ошибка конфигурации" in second_synthesis["messages"][1]["content"]
    )


def test_review_rounds_are_limited():
    llm = ScriptedLLM(
        {
            "coordinator-triage": [dispatch(("datastore", "PING"))],
            "specialist-datastore": [findings(), findings()],
            "coordinator-synthesis": [
                respond(call("submit_report", **DOWN_REPORT)),
                respond(call("submit_report", **DOWN_REPORT)),
            ],
            "critic": [verdict(False, [("datastore", "ещё раз")])],
        }
    )
    result = asyncio.run(system(llm, mas_max_review_rounds=1).investigate("symptom"))
    roles = [c["role"] for c in llm.calls]
    assert roles.count("critic") == 1 and roles.count("coordinator-synthesis") == 2
    assert result.status == "completed"
    assert result.details["rounds"][-1]["verdict"].startswith("not reviewed")


def test_triage_without_dispatch_falls_back_to_all_specialists():
    llm = ScriptedLLM(
        {
            "coordinator-triage": [respond(content="не знаю")]
            * 6,  # 4 steps + 2 forced
            "specialist-infrastructure": [findings()],
            "specialist-connectivity": [findings()],
            "specialist-datastore": [findings()],
            "coordinator-synthesis": [respond(call("submit_report", **DOWN_REPORT))],
            "critic": [verdict(True)],
        }
    )
    result = asyncio.run(system(llm).investigate("symptom"))
    assert result.details["triage_fallback"] is True
    assert len(result.details["rounds"][0]["tasks"]) == 3
    assert result.status == "completed"


def test_llm_failure_in_triage_aborts():
    llm = ScriptedLLM({"coordinator-triage": [LLMError("connection refused")]})
    result = asyncio.run(system(llm).investigate("symptom"))
    assert result.status == "llm_error" and "connection refused" in result.error
    assert result.report.category == "unknown"


def test_specialists_run_concurrently():
    started = []
    both_started = asyncio.Event()

    async def rendezvous(name):
        started.append(name)
        if len(started) == 2:
            both_started.set()
        # Deadlocks (and times out) if specialists were run one after another.
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return DiagnosticResult(tool=name, status="ok", data={})

    backend = dict(ReplayWorld.named("healthy").backend())

    async def check_redis():
        return await rendezvous("check_redis")

    async def get_service_logs(**kwargs):
        return await rendezvous("get_service_logs")

    backend.update(check_redis=check_redis, get_service_logs=get_service_logs)
    llm = ScriptedLLM(
        {
            "coordinator-triage": [
                dispatch(("datastore", "PING"), ("infrastructure", "Логи Redis"))
            ],
            "specialist-datastore": [respond(call("check_redis")), findings()],
            "specialist-infrastructure": [
                respond(call("get_service_logs", service="redis")),
                findings(),
            ],
            "coordinator-synthesis": [respond(call("submit_report", **DOWN_REPORT))],
            "critic": [verdict(True)],
        }
    )
    result = asyncio.run(system(llm, backend=backend).investigate("symptom"))
    assert sorted(started) == ["check_redis", "get_service_logs"]
    assert all(record.status == "ok" for record in result.tool_calls)
