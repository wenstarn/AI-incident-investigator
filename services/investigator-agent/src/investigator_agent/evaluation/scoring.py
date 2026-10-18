"""Scoring of a single investigation and aggregation of many."""

from statistics import mean

from pydantic import BaseModel

from investigator_agent.evaluation.cases import ExpectedDiagnosis
from investigator_agent.report import InvestigationResult
from investigator_agent.tools import ToolCallRecord

REJECTED_CALLS = frozenset({"tool_not_allowed", "invalid_arguments"})


class CaseScore(BaseModel):
    case_id: str
    system: str
    repeat: int
    correct: bool
    partial: bool
    grounded: bool
    predicted: str
    expected: str
    status: str
    tool_calls: int
    invalid_tool_calls: int
    duplicate_tool_calls: int
    llm_calls: int
    tokens: int
    duration_s: float


def diagnosis_label(category: str, component: str, dependency: str = "none") -> str:
    label = f"{category}/{component}"
    return f"{label}->{dependency}" if dependency != "none" else label


def _error_type(record: ToolCallRecord) -> str | None:
    return (record.result.get("error") or {}).get("type")


def is_correct(result: InvestigationResult, expected: ExpectedDiagnosis) -> bool:
    report = result.report
    if report.category != expected.category:
        return False
    if expected.category == "no_incident":
        return True
    if report.component != expected.component:
        return False
    if expected.category == "dependency_misconfiguration":
        return report.dependency == expected.dependency
    return True


def is_grounded(result: InvestigationResult) -> bool:
    """Every evidence item cites a tool that was actually called successfully."""
    called = {
        record.tool
        for record in result.tool_calls
        if _error_type(record) not in REJECTED_CALLS | {"tool_failure"}
    }
    evidence = result.report.evidence
    return bool(evidence) and all(item.tool in called for item in evidence)


def score(
    case_id: str,
    repeat: int,
    result: InvestigationResult,
    expected: ExpectedDiagnosis,
) -> CaseScore:
    correct = is_correct(result, expected)
    report = result.report
    return CaseScore(
        case_id=case_id,
        system=result.system,
        repeat=repeat,
        correct=correct,
        partial=not correct and report.category == expected.category,
        grounded=is_grounded(result),
        predicted=diagnosis_label(report.category, report.component, report.dependency),
        expected=diagnosis_label(
            expected.category, expected.component, expected.dependency
        ),
        status=result.status,
        tool_calls=len(result.tool_calls),
        invalid_tool_calls=sum(
            _error_type(record) in REJECTED_CALLS for record in result.tool_calls
        ),
        duplicate_tool_calls=sum(record.duplicate for record in result.tool_calls),
        llm_calls=result.llm_calls,
        tokens=result.usage.total_tokens,
        duration_s=result.duration_s,
    )


class Summary(BaseModel):
    system: str
    case_id: str
    runs: int
    correct: int
    partial: int
    grounded: int
    mean_tool_calls: float
    mean_llm_calls: float
    mean_tokens: float
    mean_duration_s: float


def summarize(scores: list[CaseScore]) -> list[Summary]:
    groups: dict[tuple[str, str], list[CaseScore]] = {}
    for item in scores:
        groups.setdefault((item.system, item.case_id), []).append(item)
    for system in sorted({item.system for item in scores}):
        groups[(system, "TOTAL")] = [item for item in scores if item.system == system]
    return [
        Summary(
            system=system,
            case_id=case_id,
            runs=len(items),
            correct=sum(item.correct for item in items),
            partial=sum(item.partial for item in items),
            grounded=sum(item.grounded for item in items),
            mean_tool_calls=round(mean(item.tool_calls for item in items), 1),
            mean_llm_calls=round(mean(item.llm_calls for item in items), 1),
            mean_tokens=round(mean(item.tokens for item in items)),
            mean_duration_s=round(mean(item.duration_s for item in items), 1),
        )
        for (system, case_id), items in groups.items()
    ]


def markdown_table(summaries: list[Summary]) -> str:
    lines = [
        "| System | Case | Correct | Partial | Grounded | Tool calls | LLM calls "
        "| Tokens | Time, s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in summaries:
        lines.append(
            f"| {item.system} | {item.case_id} | {item.correct}/{item.runs} "
            f"| {item.partial}/{item.runs} | {item.grounded}/{item.runs} "
            f"| {item.mean_tool_calls} | {item.mean_llm_calls} | {item.mean_tokens} "
            f"| {item.mean_duration_s} |"
        )
    return "\n".join(lines)
