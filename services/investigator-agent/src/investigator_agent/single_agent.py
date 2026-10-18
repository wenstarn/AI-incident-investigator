"""Baseline: one agent with all diagnostic tools in one tool-calling loop."""

import time

from investigator_agent.config import AgentConfig
from investigator_agent.events import EventRecorder, EventSink, emit
from investigator_agent.llm import LLMClient
from investigator_agent.loop import FinalTool, run_tool_loop
from investigator_agent.prompts import SINGLE_AGENT_PROMPT
from investigator_agent.report import (
    IncidentReport,
    InvestigationResult,
    finished_event,
    inconclusive_report,
)
from investigator_agent.tools import ToolRegistry

SUBMIT_REPORT = FinalTool(
    "submit_report",
    "Submit the final incident report. Call exactly once, when done.",
    IncidentReport,
)


def symptom_message(symptom: str) -> str:
    return f"Symptom report:\n{symptom}\n\nInvestigate and find the root cause."


class SingleAgent:
    name = "single"

    def __init__(
        self, llm: LLMClient, registry: ToolRegistry, config: AgentConfig
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._config = config

    async def investigate(
        self, symptom: str, on_event: EventSink | None = None
    ) -> InvestigationResult:
        started = time.monotonic()
        recorder = EventRecorder(on_event)
        emit(recorder, "investigation_started", system="single", symptom=symptom)
        outcome = await run_tool_loop(
            llm=self._llm,
            agent_name="single-investigator",
            system_prompt=SINGLE_AGENT_PROMPT,
            user_prompt=symptom_message(symptom),
            registry=self._registry,
            final_tool=SUBMIT_REPORT,
            max_steps=self._config.max_steps,
            on_event=recorder,
        )
        report = outcome.final
        if not isinstance(report, IncidentReport):
            report = inconclusive_report(
                outcome.error or "агент не представил отчёт в пределах лимита шагов"
            )
        result = InvestigationResult(
            system="single",
            symptom=symptom,
            status=outcome.status,
            error=outcome.error,
            report=report,
            tool_calls=outcome.tool_calls,
            usage=outcome.usage,
            llm_calls=outcome.llm_calls,
            duration_s=round(time.monotonic() - started, 3),
            transcript=[{"agent": "single-investigator", "messages": outcome.messages}],
        )
        emit(recorder, "investigation_finished", **finished_event(result))
        result.events = recorder.events
        return result
