"""Replay fidelity check: compare replay worlds with the live stack.

For every case the harness activates the scenario, reproduces the symptom and
runs the same probe set against the live backend and the replay world. Only
signals an agent reasons about are compared (status, error type, HTTP status,
reachability, container state, effective port); latencies and counters are
expected to differ. Probes whose latency is close to a timeout (calls to a
stopped container hang on name resolution for 3-8 s) can flip between runs, so
repeat the check before changing a replay world.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from investigator_agent.evaluation.cases import EvalCase
from investigator_agent.evaluation.controller import ControllerError
from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.evaluation.runner import (
    RECOVERY_PROBES,
    SYMPTOM_PROBES,
    Controller,
    reproduce_symptom,
    wait_until,
)
from investigator_agent.tools import Backend

PROBES: list[tuple[str, dict[str, Any]]] = [
    *(
        ("get_service_status", {"service": service})
        for service in ("user-service", "order-service", "postgres", "redis")
    ),
    ("check_postgres", {}),
    ("check_redis", {}),
    *(
        ("check_http_endpoint", {"service": service, "path": path})
        for service, path in (
            ("user-service", "/health"),
            ("user-service", "/health/db"),
            ("user-service", "/users/0"),
            ("order-service", "/health"),
            ("order-service", "/health/db"),
            ("order-service", "/health/redis"),
        )
    ),
    *(
        (
            "check_service_dependency",
            {"source_service": "order-service", "dependency": dependency},
        )
        for dependency in ("user-service", "redis")
    ),
    *(
        ("get_dependency_config", {"service": "order-service", "dependency": name})
        for name in ("user-service", "redis")
    ),
]
SIGNAL_FIELDS = ("reachable", "status_code", "container_status", "port")


class Mismatch(BaseModel):
    case_id: str
    probe: str
    live: dict[str, Any]
    replay: dict[str, Any]


def signals(result) -> dict[str, Any]:
    values = {"status": result.status}
    if result.error is not None:
        values["error"] = result.error.type
    for name in SIGNAL_FIELDS:
        if name in result.data:
            values[name] = result.data[name]
    return values


async def compare(case: EvalCase, live: Backend, replay: Backend) -> list[Mismatch]:
    mismatches = []
    for tool, arguments in PROBES:
        live_signals = signals(await live[tool](**arguments))
        replay_signals = signals(await replay[tool](**arguments))
        if live_signals != replay_signals:
            mismatches.append(
                Mismatch(
                    case_id=case.id,
                    probe=f"{tool}({arguments})",
                    live=live_signals,
                    replay=replay_signals,
                )
            )
    return mismatches


async def run_fidelity(
    cases: list[EvalCase],
    controller: Controller,
    live: Backend,
    reproduce: Callable[[EvalCase], Awaitable[None]] = reproduce_symptom,
    symptom_timeout_seconds: float = 60,
    recovery_timeout_seconds: float = 180,
) -> list[Mismatch]:
    if await controller.active() is not None:
        raise ControllerError("Reset the active incident before a fidelity check")
    mismatches: list[Mismatch] = []
    for case in cases:
        activated = False
        try:
            if case.incident_id:
                await controller.activate(case.incident_id)
                activated = True
                if not await wait_until(
                    live,
                    [SYMPTOM_PROBES[case.incident_id]],
                    "degraded",
                    symptom_timeout_seconds,
                ):
                    raise ControllerError(f"{case.id} did not become observable")
            await reproduce(case)
            mismatches += await compare(
                case, live, ReplayWorld.named(case.id).backend()
            )
        finally:
            if activated:
                await controller.reset()
                if not await wait_until(
                    live, RECOVERY_PROBES, "ok", recovery_timeout_seconds
                ):
                    raise ControllerError(f"The stack did not recover after {case.id}")
    return mismatches
