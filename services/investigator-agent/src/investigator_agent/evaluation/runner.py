"""Evaluation harness: replay (recorded worlds) and live (real stack) modes.

In live mode the harness, not the agent, talks to the Incident Controller:
it activates a scenario, waits until it is observable, reproduces the user
symptom with safe business requests, lets every system investigate, and always
resets the scenario in ``finally``.
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from diagnostic_tools.models import DiagnosticResult

from investigator_agent.evaluation.cases import SYMPTOM_REQUESTS, EvalCase
from investigator_agent.evaluation.controller import ControllerError
from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.evaluation.scoring import CaseScore, score
from investigator_agent.report import InvestigationResult
from investigator_agent.tools import Backend, ToolRegistry

logger = logging.getLogger(__name__)

# Probe that turns degraded once a scenario is observable.
SYMPTOM_PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "INC-001": ("get_service_status", {"service": "user-service"}),
    "INC-002": ("get_service_status", {"service": "redis"}),
    "INC-003": ("get_service_status", {"service": "postgres"}),
    "INC-004": (
        "check_service_dependency",
        {"source_service": "order-service", "dependency": "user-service"},
    ),
    "INC-005": (
        "check_service_dependency",
        {"source_service": "order-service", "dependency": "user-service"},
    ),
    "INC-006": (
        "check_service_dependency",
        {"source_service": "order-service", "dependency": "redis"},
    ),
}
RECOVERY_PROBES: list[tuple[str, dict[str, Any]]] = [
    ("get_service_status", {"service": service})
    for service in ("user-service", "order-service", "postgres", "redis")
] + [
    (
        "check_service_dependency",
        {"source_service": "order-service", "dependency": dependency},
    )
    for dependency in ("user-service", "redis")
]


class Investigator(Protocol):
    async def investigate(self, symptom: str) -> InvestigationResult: ...


SystemFactory = Callable[[str, ToolRegistry], Investigator]
ResultSink = Callable[[EvalCase, int, InvestigationResult, CaseScore], None]


class Controller(Protocol):
    async def active(self) -> str | None: ...
    async def activate(self, incident_id: str) -> None: ...
    async def reset(self) -> None: ...


async def _investigate_all(
    case: EvalCase,
    registry: ToolRegistry,
    systems: list[str],
    repeats: int,
    make_system: SystemFactory,
    sink: ResultSink | None,
    source: str,
) -> list[CaseScore]:
    scores = []
    for system in systems:
        for repeat in range(1, repeats + 1):
            started_at = datetime.now(UTC).isoformat()
            result = await make_system(system, registry).investigate(case.symptom)
            result.source = source
            result.details["started_at"] = started_at
            item = score(case.id, repeat, result, case.expected)
            scores.append(item)
            if sink:
                sink(case, repeat, result, item)
    return scores


async def run_replay(
    cases: list[EvalCase],
    systems: list[str],
    repeats: int,
    make_system: SystemFactory,
    max_output_chars: int = 6000,
    sink: ResultSink | None = None,
) -> list[CaseScore]:
    scores = []
    for case in cases:
        world = ReplayWorld.named(case.id)
        registry = ToolRegistry(world.backend(), max_output_chars=max_output_chars)
        scores += await _investigate_all(
            case, registry, systems, repeats, make_system, sink, "replay"
        )
    return scores


async def wait_until(
    backend: Mapping[str, Callable[..., Awaitable[DiagnosticResult]]],
    probes: list[tuple[str, dict[str, Any]]],
    expected: str,
    timeout_seconds: float,
    interval_seconds: float = 1.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        results = [await backend[tool](**args) for tool, args in probes]
        if all(result.status == expected for result in results):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval_seconds)


async def reproduce_symptom(case: EvalCase, transport=None) -> None:
    """Send the business requests a user would send. Failures are expected."""
    bases = {
        "order": os.getenv("ORDER_SERVICE_URL", "http://127.0.0.1:8002"),
        "user": os.getenv("USER_SERVICE_URL", "http://127.0.0.1:8001"),
    }
    async with httpx.AsyncClient(
        timeout=10, transport=transport, trust_env=False
    ) as client:
        for number, (base, method, path, body) in enumerate(
            SYMPTOM_REQUESTS.get(case.id, []), start=1
        ):
            try:
                await client.request(
                    method,
                    bases[base].rstrip("/") + path,
                    json=body,
                    headers={"X-Request-ID": f"eval-{case.id}-{number}"},
                )
            except httpx.HTTPError:
                pass


async def run_live(
    cases: list[EvalCase],
    systems: list[str],
    repeats: int,
    make_system: SystemFactory,
    controller: Controller,
    backend: Backend,
    max_output_chars: int = 6000,
    sink: ResultSink | None = None,
    symptom_timeout_seconds: float = 60,
    recovery_timeout_seconds: float = 180,
    reproduce: Callable[[EvalCase], Awaitable[None]] = reproduce_symptom,
) -> list[CaseScore]:
    if await controller.active() is not None:
        raise ControllerError("Reset the active incident before a live evaluation")
    registry = ToolRegistry(backend, max_output_chars=max_output_chars)
    if not await wait_until(backend, RECOVERY_PROBES, "ok", recovery_timeout_seconds):
        raise ControllerError("The stack is not healthy before the evaluation")
    scores = []
    for case in cases:
        activated = False
        try:
            if case.incident_id:
                await controller.activate(case.incident_id)
                activated = True
                probe = SYMPTOM_PROBES[case.incident_id]
                if not await wait_until(
                    backend, [probe], "degraded", symptom_timeout_seconds
                ):
                    raise ControllerError(f"{case.id} did not become observable")
            await reproduce(case)
            scores += await _investigate_all(
                case, registry, systems, repeats, make_system, sink, "live"
            )
        finally:
            if activated:
                await controller.reset()
                if not await wait_until(
                    backend, RECOVERY_PROBES, "ok", recovery_timeout_seconds
                ):
                    raise ControllerError(f"The stack did not recover after {case.id}")
    return scores


class ResultWriter:
    """Writes every investigation (with transcript) and the scores to disk."""

    def __init__(self, out_dir: Path, mode: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.directory = out_dir / f"{stamp}-{mode}"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._results = self.directory / "investigations.jsonl"

    def __call__(
        self, case: EvalCase, repeat: int, result: InvestigationResult, item: CaseScore
    ) -> None:
        record = {
            "case_id": case.id,
            "repeat": repeat,
            "score": item.model_dump(),
            "result": result.model_dump(mode="json"),
        }
        with self._results.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        mark = "OK " if item.correct else ("~  " if item.partial else "X  ")
        print(
            f"{mark} {item.system:6} {case.id:8} #{repeat}: {item.predicted} "
            f"(expected {item.expected}); tools={item.tool_calls} "
            f"tokens={item.tokens} {item.duration_s}s",
            flush=True,
        )

    def write_summary(self, scores: list[CaseScore], table: str, meta: dict) -> Path:
        (self.directory / "scores.json").write_text(
            json.dumps(
                {"meta": meta, "scores": [item.model_dump() for item in scores]},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        summary = self.directory / "summary.md"
        summary.write_text(
            "# Evaluation summary\n\n"
            + "\n".join(f"- {key}: {value}" for key, value in meta.items())
            + "\n\n"
            + table
            + "\n",
            encoding="utf-8",
        )
        return summary
