"""Critic: independent verification of the draft diagnosis. Has no tools."""

from investigator_agent.events import EventSink
from investigator_agent.llm import LLMClient
from investigator_agent.loop import FinalTool, LoopOutcome, run_tool_loop
from investigator_agent.mas.models import MAX_TASKS_PER_ROUND, Verdict
from investigator_agent.prompts import CRITIC_PROMPT
from investigator_agent.report import IncidentReport

SUBMIT_VERDICT = FinalTool(
    "submit_verdict",
    "Approve the draft report or reject it with issues and requested checks.",
    Verdict,
)
CRITIC_MAX_STEPS = 2


async def review(
    llm: LLMClient,
    symptom: str,
    draft: IncidentReport,
    observations: str,
    on_event: EventSink | None = None,
) -> tuple[Verdict | None, LoopOutcome]:
    outcome = await run_tool_loop(
        llm=llm,
        agent_name="critic",
        system_prompt=CRITIC_PROMPT,
        user_prompt=(
            f"Symptom report:\n{symptom}\n\n"
            f"Draft report:\n{draft.model_dump_json(indent=1)}\n\n"
            f"Observation log (raw tool results):\n{observations}"
        ),
        registry=None,
        final_tool=SUBMIT_VERDICT,
        max_steps=CRITIC_MAX_STEPS,
        on_event=on_event,
    )
    verdict = outcome.final if isinstance(outcome.final, Verdict) else None
    if verdict is not None:
        verdict = verdict.model_copy(
            update={"requested_checks": verdict.requested_checks[:MAX_TASKS_PER_ROUND]}
        )
    return verdict, outcome
