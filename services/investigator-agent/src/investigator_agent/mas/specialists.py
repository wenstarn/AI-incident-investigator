"""Specialist agents. Each runs its own tool loop with a restricted tool set."""

import asyncio

from investigator_agent.config import AgentConfig
from investigator_agent.events import EventSink
from investigator_agent.llm import LLMClient
from investigator_agent.loop import FinalTool, LoopOutcome, run_tool_loop
from investigator_agent.mas.models import (
    SPECIALIST_TOOLS,
    SpecialistFindings,
    SpecialistResult,
    SpecialistTask,
)
from investigator_agent.prompts import specialist_prompt
from investigator_agent.tools import ToolRegistry

SUBMIT_FINDINGS = FinalTool(
    "submit_findings",
    "Return your observations and interpretation to the coordinator.",
    SpecialistFindings,
)


async def run_specialist(
    llm: LLMClient,
    registry: ToolRegistry,
    config: AgentConfig,
    symptom: str,
    hypotheses: list[str],
    task: SpecialistTask,
    on_event: EventSink | None = None,
) -> tuple[SpecialistResult, LoopOutcome]:
    hypothesis_text = "\n".join(f"- {item}" for item in hypotheses) or "- (none)"
    outcome = await run_tool_loop(
        llm=llm,
        agent_name=f"specialist-{task.specialist}",
        system_prompt=specialist_prompt(task.specialist),
        user_prompt=(
            f"Symptom report:\n{symptom}\n\n"
            f"Coordinator hypotheses:\n{hypothesis_text}\n\n"
            f"Your task:\n{task.task}"
        ),
        registry=registry.subset(SPECIALIST_TOOLS[task.specialist]),
        final_tool=SUBMIT_FINDINGS,
        max_steps=config.mas_specialist_max_steps,
        on_event=on_event,
    )
    findings = outcome.final if isinstance(outcome.final, SpecialistFindings) else None
    return (
        SpecialistResult(
            specialist=task.specialist,
            task=task.task,
            status=outcome.status,
            findings=findings,
        ),
        outcome,
    )


async def run_specialists(
    llm: LLMClient,
    registry: ToolRegistry,
    config: AgentConfig,
    symptom: str,
    hypotheses: list[str],
    tasks: list[SpecialistTask],
    on_event: EventSink | None = None,
) -> list[tuple[SpecialistResult, LoopOutcome]]:
    """Run independent specialist tasks concurrently."""
    return list(
        await asyncio.gather(
            *(
                run_specialist(
                    llm, registry, config, symptom, hypotheses, task, on_event
                )
                for task in tasks
            )
        )
    )
