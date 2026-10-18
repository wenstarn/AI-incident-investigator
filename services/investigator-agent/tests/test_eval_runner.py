import asyncio
import json

import httpx
import pytest
from investigator_agent.evaluation.cases import MISSING_USER_ID, select_cases
from investigator_agent.evaluation.controller import (
    ControllerError,
    IncidentControllerClient,
)
from investigator_agent.evaluation.fidelity import run_fidelity
from investigator_agent.evaluation.replay import WORLD_STATES, ReplayWorld
from investigator_agent.evaluation.runner import (
    ResultWriter,
    reproduce_symptom,
    run_live,
    run_replay,
)
from investigator_agent.evaluation.scoring import markdown_table, summarize
from investigator_agent.llm import Usage
from investigator_agent.report import IncidentReport, InvestigationResult


class RuleBasedInvestigator:
    """Offline stand-in for an LLM agent: a fixed decision procedure."""

    def __init__(self, name, registry):
        self.name = name
        self.registry = registry

    async def check(self, tool, **arguments):
        content, record = await self.registry.execute(tool, arguments, self.name)
        self.records.append(record)
        return json.loads(content)

    async def investigate(self, symptom):
        self.records = []
        report = {"summary": "s", "confidence": 0.5, "dependency": "none"}
        user = await self.check("get_service_status", service="user-service")
        redis = await self.check("check_redis")
        postgres = await self.check("check_postgres")
        user_dep = await self.check(
            "check_service_dependency",
            source_service="order-service",
            dependency="user-service",
        )
        redis_dep = await self.check(
            "check_service_dependency",
            source_service="order-service",
            dependency="redis",
        )
        if user["status"] != "ok":
            report.update(category="component_down", component="user-service")
        elif redis["status"] != "ok":
            report.update(category="component_down", component="redis")
        elif postgres["status"] != "ok":
            report.update(category="component_down", component="postgres")
        elif user_dep["status"] != "ok" and user_dep["error"]["type"] == "timeout":
            report.update(category="dependency_slow", component="user-service")
        elif user_dep["status"] != "ok":
            report.update(
                category="dependency_misconfiguration",
                component="order-service",
                dependency="user-service",
            )
        elif redis_dep["status"] != "ok":
            report.update(
                category="dependency_misconfiguration",
                component="order-service",
                dependency="redis",
            )
        else:
            report.update(category="no_incident", component="none")
        report["evidence"] = [{"tool": "check_redis", "observation": "o"}]
        return InvestigationResult(
            system=self.name,
            symptom=symptom,
            status="completed",
            report=IncidentReport(**report),
            tool_calls=self.records,
            usage=Usage(prompt_tokens=1, completion_tokens=1),
            llm_calls=1,
            duration_s=0.01,
        )


def test_replay_run_scores_every_case_system_and_repeat():
    sunk = []
    scores = asyncio.run(
        run_replay(
            select_cases(None),
            ["single", "mas"],
            2,
            RuleBasedInvestigator,
            sink=lambda *args: sunk.append(args),
        )
    )
    assert len(scores) == len(WORLD_STATES) * 2 * 2 == len(sunk)
    assert all(result.source == "replay" for _, _, result, _ in sunk)
    assert all(result.details["started_at"] for _, _, result, _ in sunk)
    assert all(item.correct for item in scores), [
        (item.case_id, item.predicted) for item in scores if not item.correct
    ]
    assert "| single | TOTAL | 14/14 |" in markdown_table(summarize(scores))


class FakeController:
    """Simulates the Incident Controller by switching the replay world state."""

    def __init__(self, world: ReplayWorld, active=None, effective=True):
        self.world = world
        self.events = []
        self._active = active
        self._effective = effective

    async def active(self):
        self.events.append("active")
        return self._active

    async def activate(self, incident_id):
        self.events.append(f"activate:{incident_id}")
        if self._effective:
            self.world.state = WORLD_STATES[incident_id]

    async def reset(self):
        self.events.append("reset")
        self.world.state = WORLD_STATES["healthy"]


def live_setup(**controller_kwargs):
    world = ReplayWorld.named("healthy")
    controller = FakeController(world, **controller_kwargs)
    reproduced = []

    async def reproduce(case):
        reproduced.append(case.id)

    return world, controller, reproduced, reproduce


def test_live_run_activates_and_resets_each_incident():
    world, controller, reproduced, reproduce = live_setup()
    sunk = []
    scores = asyncio.run(
        run_live(
            select_cases(["INC-001", "INC-006", "healthy"]),
            ["single"],
            1,
            RuleBasedInvestigator,
            controller,
            world.backend(),
            reproduce=reproduce,
            sink=lambda *args: sunk.append(args),
        )
    )
    assert all(result.source == "live" for _, _, result, _ in sunk)
    assert [item.correct for item in scores] == [True, True, True]
    assert controller.events == [
        "active",
        "activate:INC-001",
        "reset",
        "activate:INC-006",
        "reset",
    ]
    assert reproduced == ["INC-001", "INC-006", "healthy"]


def test_live_run_resets_even_if_investigation_fails():
    world, controller, _, reproduce = live_setup()

    class Exploding:
        def __init__(self, *args):
            pass

        async def investigate(self, symptom):
            raise RuntimeError("agent crashed")

    with pytest.raises(RuntimeError, match="agent crashed"):
        asyncio.run(
            run_live(
                select_cases(["INC-002"]),
                ["single"],
                1,
                Exploding,
                controller,
                world.backend(),
                reproduce=reproduce,
            )
        )
    assert controller.events[-1] == "reset"
    assert world.state == WORLD_STATES["healthy"]


def test_live_run_refuses_when_incident_already_active():
    world, controller, _, reproduce = live_setup(active="INC-003")
    with pytest.raises(ControllerError, match="Reset the active incident"):
        asyncio.run(
            run_live(
                select_cases(["INC-001"]),
                ["single"],
                1,
                RuleBasedInvestigator,
                controller,
                world.backend(),
                reproduce=reproduce,
            )
        )
    assert controller.events == ["active"]


def test_live_run_aborts_when_incident_is_not_observable():
    world, controller, _, reproduce = live_setup(effective=False)
    with pytest.raises(ControllerError, match="did not become observable"):
        asyncio.run(
            run_live(
                select_cases(["INC-004"]),
                ["single"],
                1,
                RuleBasedInvestigator,
                controller,
                world.backend(),
                reproduce=reproduce,
                symptom_timeout_seconds=0,
            )
        )
    assert controller.events[-1] == "reset"


def test_reproduce_symptom_sends_safe_requests():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(502)

    case = select_cases(["INC-004"])[0]
    asyncio.run(reproduce_symptom(case, transport=httpx.MockTransport(handler)))
    assert [(r.method, r.url.path) for r in requests] == [("POST", "/orders")] * 3
    assert json.loads(requests[0].content)["user_id"] == MISSING_USER_ID
    assert requests[0].headers["X-Request-ID"] == "eval-INC-004-1"

    requests.clear()
    asyncio.run(
        reproduce_symptom(
            select_cases(["healthy"])[0], transport=httpx.MockTransport(handler)
        )
    )
    assert requests == []


def test_controller_client_uses_token_and_reports_errors():
    seen = []

    def handler(request):
        seen.append(
            (request.method, request.url.path, request.headers["X-Incident-Token"])
        )
        if request.url.path == "/incidents/active":
            return httpx.Response(200, json={"incident": {"id": "INC-002"}})
        if request.url.path.endswith("/activate"):
            return httpx.Response(409, json={"detail": "busy"})
        return httpx.Response(200, json={"incident": None})

    client = IncidentControllerClient(
        "http://controller.test", "token-1", transport=httpx.MockTransport(handler)
    )
    assert asyncio.run(client.active()) == "INC-002"
    with pytest.raises(ControllerError, match="409"):
        asyncio.run(client.activate("INC-001"))
    asyncio.run(client.reset())
    assert seen[-1] == ("POST", "/incidents/active/reset", "token-1")
    with pytest.raises(ControllerError):
        IncidentControllerClient("http://controller.test", "")


def test_result_writer_saves_results_and_summary(tmp_path, capsys):
    writer = ResultWriter(tmp_path, "replay")
    case = select_cases(["INC-002"])[0]
    scores = asyncio.run(
        run_replay([case], ["single"], 1, RuleBasedInvestigator, sink=writer)
    )
    summary = writer.write_summary(
        scores, markdown_table(summarize(scores)), {"mode": "replay"}
    )
    lines = (writer.directory / "investigations.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["score"]["correct"] is True
    assert "| single | INC-002 | 1/1 |" in summary.read_text()
    assert (
        json.loads((writer.directory / "scores.json").read_text())["meta"]["mode"]
        == "replay"
    )
    assert "OK" in capsys.readouterr().out


def test_fidelity_check_finds_no_mismatch_when_live_equals_replay():
    world, controller, _, reproduce = live_setup()
    mismatches = asyncio.run(
        run_fidelity(
            select_cases(None), controller, world.backend(), reproduce=reproduce
        )
    )
    assert mismatches == []
    assert controller.events[-1] == "reset"


def test_fidelity_check_reports_differences():
    world, controller, _, reproduce = live_setup()
    real = world.backend()

    async def wrong_port(**kwargs):
        result = await real["get_dependency_config"](**kwargs)
        return result.model_copy(update={"data": {**result.data, "port": 1}})

    live = {**real, "get_dependency_config": wrong_port}
    mismatches = asyncio.run(
        run_fidelity(select_cases(["INC-004"]), controller, live, reproduce=reproduce)
    )
    assert len(mismatches) == 2
    assert all(m.probe.startswith("get_dependency_config(") for m in mismatches)
    assert mismatches[0].live["port"] == 1
