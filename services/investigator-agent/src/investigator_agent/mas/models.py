"""Messages exchanged between MAS roles."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from investigator_agent.report import Evidence
from investigator_agent.tools import ToolCallRecord

SpecialistName = Literal["infrastructure", "connectivity", "datastore"]

# Least privilege: each specialist sees only the tools of its area.
SPECIALIST_TOOLS: dict[str, tuple[str, ...]] = {
    "infrastructure": ("get_service_status", "get_service_logs"),
    "connectivity": (
        "check_http_endpoint",
        "check_service_dependency",
        "get_dependency_config",
    ),
    "datastore": ("check_postgres", "check_redis", "get_service_metrics"),
}
TRIAGE_TOOLS = ("get_service_status",)
MAX_TASKS_PER_ROUND = 3
MAX_OBSERVATION_CHARS = 1500


class SpecialistTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialist: SpecialistName
    task: str = Field(description="Concrete question the specialist must answer")


class DispatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypotheses: list[str] = Field(description="Candidate causes to discriminate")
    tasks: list[SpecialistTask] = Field(min_length=1)


class SpecialistFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[Evidence] = Field(description="What the tools showed")
    interpretation: str = Field(description="What the observations mean")
    suspected_cause: str = Field(default="", description="Cause suggested, if any")
    confidence: float = Field(ge=0, le=1)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    issues: list[str] = Field(default_factory=list)
    requested_checks: list[SpecialistTask] = Field(default_factory=list)


class SpecialistResult(BaseModel):
    specialist: SpecialistName
    task: str
    status: str
    findings: SpecialistFindings | None = None


def observation_log(records: list[ToolCallRecord]) -> str:
    lines = []
    for number, record in enumerate(records, start=1):
        arguments = json.dumps(record.arguments, ensure_ascii=False)
        result = json.dumps(record.result, ensure_ascii=False)
        if len(result) > MAX_OBSERVATION_CHARS:
            result = result[:MAX_OBSERVATION_CHARS] + "...(truncated)"
        lines.append(
            f"#{number} [{record.agent}] {record.tool}({arguments}) -> {result}"
        )
    return "\n".join(lines) or "(no observations)"
