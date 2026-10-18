"""Coordinator: triage and dispatch, then synthesis of the final report."""

import json

from investigator_agent.config import AgentConfig
from investigator_agent.events import EventSink
from investigator_agent.llm import LLMClient
from investigator_agent.loop import FinalTool, LoopOutcome, run_tool_loop
from investigator_agent.mas.models import (
    MAX_TASKS_PER_ROUND,
    TRIAGE_TOOLS,
    DispatchPlan,
    SpecialistResult,
    SpecialistTask,
)
from investigator_agent.prompts import (
    COORDINATOR_SYNTHESIS_PROMPT,
    COORDINATOR_TRIAGE_PROMPT,
)
from investigator_agent.report import IncidentReport
from investigator_agent.single_agent import SUBMIT_REPORT, symptom_message
from investigator_agent.tools import ToolRegistry

DISPATCH = FinalTool(
    "dispatch",
    "Hand the investigation to specialists: hypotheses and concrete tasks.",
    DispatchPlan,
)
TRIAGE_MAX_STEPS = 4
SYNTHESIS_MAX_STEPS = 2


def fallback_plan() -> DispatchPlan:
    """Used when triage fails to dispatch: ask every specialist."""
    return DispatchPlan(
        hypotheses=[],
        tasks=[
            SpecialistTask(
                specialist=name,
                task="Investigate the symptom within your area and report findings.",
            )
            for name in ("infrastructure", "connectivity", "datastore")
        ],
    )


async def triage(
    llm: LLMClient,
    registry: ToolRegistry,
    config: AgentConfig,
    symptom: str,
    on_event: EventSink | None = None,
) -> tuple[DispatchPlan | None, LoopOutcome]:
    outcome = await run_tool_loop(
        llm=llm,
        agent_name="coordinator-triage",
        system_prompt=COORDINATOR_TRIAGE_PROMPT,
        user_prompt=symptom_message(symptom),
        registry=registry.subset(TRIAGE_TOOLS),
        final_tool=DISPATCH,
        max_steps=min(TRIAGE_MAX_STEPS, config.max_steps),
        on_event=on_event,
    )
    plan = outcome.final if isinstance(outcome.final, DispatchPlan) else None
    if plan is not None:
        plan = plan.model_copy(update={"tasks": plan.tasks[:MAX_TASKS_PER_ROUND]})
    return plan, outcome


async def synthesize(
    llm: LLMClient,
    symptom: str,
    hypotheses: list[str],
    specialist_results: list[SpecialistResult],
    observations: str,
    critic_issues: list[str],
    on_event: EventSink | None = None,
) -> tuple[IncidentReport | None, LoopOutcome]:
    findings = json.dumps(
        [result.model_dump(mode="json") for result in specialist_results],
        ensure_ascii=False,
        indent=1,
    )
    issues = "\n".join(f"- {issue}" for issue in critic_issues) or "- (none)"
    hypothesis_text = "\n".join(f"- {item}" for item in hypotheses) or "- (none)"
    outcome = await run_tool_loop(
        llm=llm,
        agent_name="coordinator-synthesis",
        system_prompt=COORDINATOR_SYNTHESIS_PROMPT,
        user_prompt=(
            f"Symptom report:\n{symptom}\n\n"
            f"Hypotheses:\n{hypothesis_text}\n\n"
            f"Specialist findings:\n{findings}\n\n"
            f"Observation log (raw tool results):\n{observations}\n\n"
            f"Critic issues to address:\n{issues}"
        ),
        registry=None,
        final_tool=SUBMIT_REPORT,
        max_steps=SYNTHESIS_MAX_STEPS,
        on_event=on_event,
    )
    report = outcome.final if isinstance(outcome.final, IncidentReport) else None
    return report, outcome
