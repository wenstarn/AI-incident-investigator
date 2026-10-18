"""Multi-agent investigation: coordinator -> parallel specialists -> critic.

Flow::

    triage (coordinator, get_service_status) --dispatch--> specialists (parallel)
        -> synthesis (coordinator) -> critic --approved--> report
                         ^                 |
                         +-- requested ----+  (at most MAS_MAX_REVIEW_ROUNDS)
                             checks
"""

import time
from typing import Any

from investigator_agent.config import AgentConfig
from investigator_agent.events import EventRecorder, EventSink, emit
from investigator_agent.llm import LLMClient, Usage
from investigator_agent.loop import LoopOutcome
from investigator_agent.mas import coordinator, critic
from investigator_agent.mas.models import SpecialistResult, observation_log
from investigator_agent.mas.specialists import run_specialists
from investigator_agent.report import (
    IncidentReport,
    InvestigationResult,
    finished_event,
    inconclusive_report,
)
from investigator_agent.tools import ToolCallRecord, ToolRegistry


class _Run:
    """Accumulates usage, tool calls and transcripts of all roles."""

    def __init__(self, on_event: EventSink | None) -> None:
        self.events = EventRecorder(on_event)
        self.usage = Usage()
        self.llm_calls = 0
        self.tool_calls: list[ToolCallRecord] = []
        self.transcript: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def add(self, agent: str, outcome: LoopOutcome) -> None:
        self.usage.add(outcome.usage)
        self.llm_calls += outcome.llm_calls
        self.tool_calls.extend(outcome.tool_calls)
        self.transcript.append(
            {"agent": agent, "status": outcome.status, "messages": outcome.messages}
        )
        if outcome.error:
            self.errors.append(f"{agent}: {outcome.error}")


class MultiAgentSystem:
    name = "mas"

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
        run = _Run(on_event)
        emit(run.events, "investigation_started", system="mas", symptom=symptom)
        details: dict[str, Any] = {"rounds": [], "triage_fallback": False}

        plan, outcome = await coordinator.triage(
            self._llm, self._registry, self._config, symptom, run.events
        )
        run.add("coordinator-triage", outcome)
        if outcome.status == "llm_error":
            return self._result(symptom, started, run, details, None, "llm_error")
        if plan is None:
            plan = coordinator.fallback_plan()
            details["triage_fallback"] = True
        details["hypotheses"] = plan.hypotheses

        results: list[SpecialistResult] = []
        tasks = plan.tasks
        critic_issues: list[str] = []
        report: IncidentReport | None = None
        llm_failed = False
        for round_number in range(self._config.mas_max_review_rounds + 1):
            round_info: dict[str, Any] = {
                "round": round_number + 1,
                "tasks": [task.model_dump() for task in tasks],
            }
            details["rounds"].append(round_info)
            emit(run.events, "round", round=round_number + 1, tasks=round_info["tasks"])
            for result, specialist_outcome in await run_specialists(
                self._llm,
                self._registry,
                self._config,
                symptom,
                plan.hypotheses,
                tasks,
                run.events,
            ):
                results.append(result)
                run.add(f"specialist-{result.specialist}", specialist_outcome)

            draft, outcome = await coordinator.synthesize(
                self._llm,
                symptom,
                plan.hypotheses,
                results,
                observation_log(run.tool_calls),
                critic_issues,
                run.events,
            )
            run.add("coordinator-synthesis", outcome)
            if draft is None:
                llm_failed = outcome.status == "llm_error"
                break
            report = draft
            if round_number == self._config.mas_max_review_rounds:
                round_info["verdict"] = "not reviewed: round limit reached"
                break

            verdict, outcome = await critic.review(
                self._llm, symptom, draft, observation_log(run.tool_calls), run.events
            )
            run.add("critic", outcome)
            if verdict is None:
                round_info["verdict"] = "critic failed; draft accepted"
                break
            round_info["verdict"] = verdict.model_dump()
            if verdict.approved:
                break
            critic_issues = verdict.issues
            tasks = verdict.requested_checks
            if not tasks:
                # Rejected without new checks: one more synthesis on same data.
                continue

        status = (
            "completed" if report else ("llm_error" if llm_failed else "step_limit")
        )
        return self._result(symptom, started, run, details, report, status)

    @staticmethod
    def _result(
        symptom: str,
        started: float,
        run: _Run,
        details: dict[str, Any],
        report: IncidentReport | None,
        status: str,
    ) -> InvestigationResult:
        error = "; ".join(run.errors) or None
        result = InvestigationResult(
            system="mas",
            symptom=symptom,
            status=status,
            error=error if status != "completed" else None,
            report=report
            or inconclusive_report(error or "координатор не сформировал отчёт"),
            tool_calls=run.tool_calls,
            usage=run.usage,
            llm_calls=run.llm_calls,
            duration_s=round(time.monotonic() - started, 3),
            transcript=run.transcript,
            details={**details, "role_errors": run.errors},
        )
        emit(run.events, "investigation_finished", **finished_event(result))
        result.events = run.events.events
        return result
