from investigator_agent.evaluation.cases import (
    CASES,
    CASES_BY_ID,
    GENERIC_SYMPTOM,
    select_cases,
)
from investigator_agent.evaluation.scoring import markdown_table, score, summarize
from investigator_agent.llm import Usage
from investigator_agent.report import IncidentReport, InvestigationResult
from investigator_agent.tools import ToolCallRecord


def record(tool, error_type=None):
    error = {"type": error_type, "message": "x"} if error_type else None
    return ToolCallRecord(
        agent="a",
        tool=tool,
        arguments={},
        status="error" if error_type else "ok",
        result={"tool": tool, "status": "ok", "data": {}, "error": error},
        duration_ms=1,
    )


def result(
    category, component, dependency="none", evidence=("check_redis",), calls=None
):
    return InvestigationResult(
        system="single",
        symptom="s",
        status="completed",
        report=IncidentReport(
            summary="s",
            category=category,
            component=component,
            dependency=dependency,
            evidence=[{"tool": tool, "observation": "o"} for tool in evidence],
            confidence=0.5,
        ),
        tool_calls=calls if calls is not None else [record("check_redis")],
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        llm_calls=2,
        duration_s=1.5,
    )


def test_correct_partial_and_wrong():
    expected = CASES_BY_ID["INC-006"].expected
    ok = score(
        "INC-006",
        1,
        result("dependency_misconfiguration", "order-service", "redis"),
        expected,
    )
    assert ok.correct and not ok.partial and ok.tokens == 15
    wrong_dep = score(
        "INC-006",
        1,
        result("dependency_misconfiguration", "order-service", "user-service"),
        expected,
    )
    assert not wrong_dep.correct and wrong_dep.partial
    wrong = score("INC-006", 1, result("component_down", "redis"), expected)
    assert not wrong.correct and not wrong.partial
    assert wrong.predicted == "component_down/redis"
    assert wrong.expected == "dependency_misconfiguration/order-service->redis"


def test_no_incident_ignores_component():
    expected = CASES_BY_ID["healthy"].expected
    assert score("healthy", 1, result("no_incident", "redis"), expected).correct


def test_grounding_requires_called_tools():
    expected = CASES_BY_ID["INC-002"].expected
    assert score("INC-002", 1, result("component_down", "redis"), expected).grounded
    invented = result("component_down", "redis", evidence=("check_postgres",))
    assert not score("INC-002", 1, invented, expected).grounded
    empty = result("component_down", "redis", evidence=())
    assert not score("INC-002", 1, empty, expected).grounded
    rejected = result(
        "component_down", "redis", calls=[record("check_redis", "tool_not_allowed")]
    )
    item = score("INC-002", 1, rejected, expected)
    assert not item.grounded and item.invalid_tool_calls == 1


def test_summary_and_table():
    expected = CASES_BY_ID["INC-002"].expected
    scores = [
        score("INC-002", 1, result("component_down", "redis"), expected),
        score("INC-002", 2, result("component_down", "postgres"), expected),
    ]
    summaries = summarize(scores)
    total = next(s for s in summaries if s.case_id == "TOTAL")
    assert total.runs == 2 and total.correct == 1 and total.partial == 1
    table = markdown_table(summaries)
    assert "| single | INC-002 | 1/2 |" in table


def test_cases_cover_all_incidents_and_hide_causes():
    assert {case.incident_id for case in CASES} == {
        "INC-001",
        "INC-002",
        "INC-003",
        "INC-004",
        "INC-005",
        "INC-006",
        None,
    }
    assert CASES_BY_ID["INC-001"].symptom == CASES_BY_ID["INC-004"].symptom
    assert CASES_BY_ID["INC-002"].symptom == CASES_BY_ID["INC-006"].symptom
    for case in CASES:
        lowered = case.symptom.lower()
        assert "inc-" not in lowered and "9999" not in lowered
        assert "остановлен" not in lowered and "порт" not in lowered


def test_select_cases():
    assert [c.id for c in select_cases(["INC-004", "healthy"])] == [
        "INC-004",
        "healthy",
    ]
    assert all(c.symptom == GENERIC_SYMPTOM for c in select_cases(None, True))
    try:
        select_cases(["INC-999"])
    except ValueError as error:
        assert "INC-999" in str(error)
    else:
        raise AssertionError("unknown case accepted")
