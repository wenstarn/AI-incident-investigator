"""Baseline: the model answers from the symptom alone, without diagnostic tools."""

import time

from investigator_agent.events import EventRecorder, EventSink, emit
from investigator_agent.llm import LLMClient
from investigator_agent.loop import run_tool_loop
from investigator_agent.prompts import LLM_ONLY_PROMPT
from investigator_agent.report import (
    IncidentReport,
    InvestigationResult,
    finished_event,
    inconclusive_report,
)
from investigator_agent.single_agent import SUBMIT_REPORT, symptom_message

# One answer and one retry after a validation error, as for the critic.
LLM_ONLY_MAX_STEPS = 2


class LLMOnly:
    name = "llm_only"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def investigate(
        self, symptom: str, on_event: EventSink | None = None
    ) -> InvestigationResult:
        started = time.monotonic()
        recorder = EventRecorder(on_event)
        emit(recorder, "investigation_started", system="llm_only", symptom=symptom)
        outcome = await run_tool_loop(
            llm=self._llm,
            agent_name="llm-only",
            system_prompt=LLM_ONLY_PROMPT,
            user_prompt=symptom_message(symptom),
            registry=None,
            final_tool=SUBMIT_REPORT,
            max_steps=LLM_ONLY_MAX_STEPS,
            on_event=recorder,
        )
        report = outcome.final
        if not isinstance(report, IncidentReport):
            report = inconclusive_report(outcome.error or "модель не представила отчёт")
        result = InvestigationResult(
            system="llm_only",
            symptom=symptom,
            status=outcome.status,
            error=outcome.error,
            report=report,
            tool_calls=outcome.tool_calls,
            usage=outcome.usage,
            llm_calls=outcome.llm_calls,
            duration_s=round(time.monotonic() - started, 3),
            transcript=[{"agent": "llm-only", "messages": outcome.messages}],
        )
        emit(recorder, "investigation_finished", **finished_event(result))
        result.events = recorder.events
        return result
