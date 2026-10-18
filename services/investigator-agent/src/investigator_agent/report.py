"""Structured investigation report and run result."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from investigator_agent.llm import Usage
from investigator_agent.tools import ToolCallRecord

Category = Literal[
    "component_down",
    "dependency_misconfiguration",
    "dependency_slow",
    "no_incident",
    "unknown",
]
Component = Literal[
    "user-service", "order-service", "postgres", "redis", "frontend", "none"
]
DependencyTarget = Literal["user-service", "redis", "postgres", "none"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(description="Name of the tool that produced the observation")
    observation: str = Field(description="What exactly the tool showed")


class IncidentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="Short diagnosis in one or two sentences")
    category: Category = Field(
        description=(
            "component_down: a container/process is stopped or not responding; "
            "dependency_misconfiguration: a service uses a wrong address/port for a "
            "healthy dependency; dependency_slow: a dependency is up but answers "
            "slower than the client timeout; no_incident: everything checked is "
            "healthy; unknown: evidence is insufficient"
        )
    )
    component: Component = Field(
        description=(
            "Where the defect lives: the stopped or slow service, or the service "
            "whose configuration is wrong; none for no_incident"
        )
    )
    dependency: DependencyTarget = Field(
        default="none",
        description=(
            "For dependency_misconfiguration: the dependency that the wrong "
            "configuration points to; otherwise none"
        ),
    )
    evidence: list[Evidence] = Field(
        default_factory=list, description="Observations supporting the diagnosis"
    )
    ruled_out: list[str] = Field(
        default_factory=list,
        description=(
            "Alternative causes with similar symptoms and why they were excluded"
        ),
    )
    recommendation: str = Field(default="", description="What the operator should do")
    confidence: float = Field(ge=0, le=1, description="Confidence from 0 to 1")


def inconclusive_report(reason: str) -> IncidentReport:
    return IncidentReport(
        summary=f"Расследование не завершено: {reason}",
        category="unknown",
        component="none",
        confidence=0,
    )


class InvestigationResult(BaseModel):
    system: Literal["llm_only", "single", "mas"]
    symptom: str
    # Where the tools pointed: the live stack or a replay world (set by caller).
    source: Literal["live", "replay"] | None = None
    status: Literal["completed", "step_limit", "llm_error"]
    error: str | None = None
    report: IncidentReport
    tool_calls: list[ToolCallRecord]
    usage: Usage
    llm_calls: int
    duration_s: float
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)


def finished_event(result: "InvestigationResult") -> dict[str, Any]:
    """Compact summary of a finished run for the event stream."""
    return {
        "system": result.system,
        "status": result.status,
        "error": result.error,
        "report": result.report.model_dump(mode="json"),
        "tool_calls": len(result.tool_calls),
        "llm_calls": result.llm_calls,
        "tokens": result.usage.total_tokens,
        "duration_s": result.duration_s,
    }
