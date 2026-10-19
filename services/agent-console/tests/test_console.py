import json

import pytest
from agent_console.main import Settings, create_app
from fastapi.testclient import TestClient
from investigator_agent.evaluation.controller import ControllerError

# The replay world stands in for the live stack: tests must not need Docker.
from investigator_agent.evaluation.replay import WORLD_STATES, ReplayWorld
from investigator_agent.llm import LLMError
from investigator_agent.testing import ScriptedLLM, call, respond

REPORT = {
    "summary": "Redis остановлен",
    "category": "component_down",
    "component": "redis",
    "evidence": [{"tool": "check_redis", "observation": "refused"}],
    "confidence": 0.8,
}


GUESS = {
    "summary": "Вероятно, остановлен User Service",
    "category": "component_down",
    "component": "user-service",
    "confidence": 0.5,
}


def scripted(_config):
    return ScriptedLLM(
        {
            "single-investigator": [
                respond(call("check_redis"), content="Проверю Redis"),
                respond(call("submit_report", **REPORT)),
            ],
            "llm-only": [respond(call("submit_report", **GUESS))],
        }
    )


class FakeController:
    def __init__(self, world):
        self.world = world
        self.events = []
        self.current = None

    async def active(self):
        return self.current

    async def activate(self, incident_id):
        if self.current:
            raise ControllerError("Incident Controller returned HTTP 409")
        self.events.append(f"activate:{incident_id}")
        self.current = incident_id
        self.world.state = WORLD_STATES[incident_id]

    async def reset(self):
        self.events.append("reset")
        self.current = None
        self.world.state = WORLD_STATES["healthy"]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("INCIDENT_CONTROLLER_TOKEN", "token")
    world = ReplayWorld.named("healthy")
    controller = FakeController(world)
    reproduced = []

    async def reproduce(case):
        reproduced.append(case.id)

    app = create_app(
        Settings(
            save_dir=tmp_path / "investigations",
            trace_dirs=[tmp_path / "investigations", tmp_path / "eval"],
        ),
        llm_factory=scripted,
        live_backend_factory=world.backend,
        controller_factory=lambda: controller,
        reproduce=reproduce,
        symptom_timeout_seconds=0,
        recovery_timeout_seconds=0,
    )
    with local_client(app) as client:
        yield client, controller, reproduced, tmp_path


def local_client(app):
    """A client that talks to the console like its own page does."""
    return TestClient(
        app, base_url="http://127.0.0.1:3010", headers={"X-Agent-Console": "1"}
    )


def read_stream(client, url):
    events = []
    with client.stream("GET", url) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event: end"):
                break
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def test_page_and_static_assets(setup):
    client, *_ = setup
    assert "Agent Console" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_options(setup):
    client, *_ = setup
    options = client.get("/api/options").json()
    assert options["model"] == "test-model" and options["controller"] is True
    assert [s["id"] for s in options["systems"]] == ["llm_only", "single", "mas"]
    assert "worlds" not in options and len(options["incidents"]) == 6
    assert any(c["id"] == "healthy" for c in options["cases"])


def test_console_offers_live_stack_only(setup):
    client, *_ = setup
    page = client.get("/").text + client.get("/static/app.js").text
    assert "replay" not in page.lower() and "world-select" not in page


def break_stand(controller, incident_id):
    """Simulate an incident switched on with the console's break button."""
    controller.current = incident_id
    controller.world.state = WORLD_STATES[incident_id]


def test_live_investigation_streams_events_and_is_saved(setup):
    client, controller, _, tmp_path = setup
    break_stand(controller, "INC-002")
    response = client.post(
        "/api/investigations", json={"symptom": "Ошибки Redis", "system": "single"}
    )
    assert response.status_code == 200
    events = read_stream(client, f"/api/investigations/{response.json()['id']}/stream")
    types = [event["type"] for event in events]
    assert events[0]["type"] == "console_started" and events[0]["source"] == "live"
    for expected in (
        "investigation_started",
        "llm_response",
        "tool_result",
        "final",
        "investigation_finished",
    ):
        assert expected in types
    evaluation = next(e for e in events if e["type"] == "evaluation")
    assert evaluation["correct"] is True and evaluation["case_id"] == "INC-002"
    saved = next(e for e in events if e["type"] == "saved")
    stored = json.loads((tmp_path / "investigations" / saved["file"]).read_text())
    assert stored["events"] and stored["report"]["component"] == "redis"
    assert stored["source"] == "live"
    assert stored["details"]["evaluation"]["correct"] is True
    assert stored["details"]["started_at"]
    assert stored["details"]["model"] == "test-model"
    trace = client.get("/api/traces").json()[0]
    assert trace["correct"] is True and trace["case_id"] == "INC-002"
    assert trace["model"] == "test-model"
    playback = read_stream(client, f"/api/traces/{trace['id']}/stream?speed=1000")
    assert playback[0]["type"] == "console_started"
    assert playback[0]["model"] == "test-model"
    verdict = next(e for e in playback if e["type"] == "evaluation")
    assert verdict["correct"] is True and verdict["case_id"] == "INC-002"
    assert all("t" in event for event in events)


def test_llm_only_answers_without_tools(setup):
    client, controller, _, _ = setup
    break_stand(controller, "INC-004")
    response = client.post(
        "/api/investigations",
        json={"symptom": "POST /orders отвечает 502", "system": "llm_only"},
    )
    events = read_stream(client, f"/api/investigations/{response.json()['id']}/stream")
    assert not any(event["type"] == "tool_result" for event in events)
    finished = next(e for e in events if e["type"] == "investigation_finished")
    assert finished["system"] == "llm_only" and finished["tool_calls"] == 0
    evaluation = next(e for e in events if e["type"] == "evaluation")
    assert evaluation["case_id"] == "INC-004" and evaluation["correct"] is False


def test_expected_answer_comes_from_active_incident(setup):
    client, controller, _, _ = setup
    break_stand(controller, "INC-001")
    run = client.post("/api/investigations", json={"symptom": "502"}).json()
    events = read_stream(client, f"/api/investigations/{run['id']}/stream")
    evaluation = next(e for e in events if e["type"] == "evaluation")
    assert evaluation["case_id"] == "INC-001" and evaluation["correct"] is False


def test_invalid_requests(setup):
    client, *_ = setup
    assert client.post("/api/investigations", json={"symptom": "x"}).status_code == 422
    bad_system = {"symptom": "abc", "system": "swarm"}
    assert client.post("/api/investigations", json=bad_system).status_code == 422
    assert client.get("/api/investigations/nope/stream").status_code == 404


def test_llm_configuration_error_is_reported(tmp_path, monkeypatch):
    def broken(_config):
        raise LLMError("LLM_MODEL is not configured")

    app = create_app(
        Settings(save_dir=tmp_path, trace_dirs=[tmp_path]), llm_factory=broken
    )
    with local_client(app) as client:
        response = client.post("/api/investigations", json={"symptom": "abc"})
    assert response.status_code == 400 and "LLM_MODEL" in response.json()["detail"]


def write_eval_run(tmp_path, folder, result, case_id="INC-002"):
    eval_dir = tmp_path / "eval" / folder
    eval_dir.mkdir(parents=True)
    record = {
        "case_id": case_id,
        "score": {"correct": True, "expected": "e", "predicted": "p"},
        "result": result,
    }
    (eval_dir / "investigations.jsonl").write_text(json.dumps(record) + "\n{broken\n")
    meta = {"meta": {"model": "eval-model", "mode": folder.rsplit("-", 1)[-1]}}
    (eval_dir / "scores.json").write_text(json.dumps(meta))


def test_traces_listing_and_playback_show_live_runs_only(setup):
    client, controller, _, tmp_path = setup
    break_stand(controller, "INC-002")
    run = client.post("/api/investigations", json={"symptom": "Ошибки Redis"}).json()
    read_stream(client, f"/api/investigations/{run['id']}/stream")
    saved = json.loads(next((tmp_path / "investigations").glob("*.json")).read_text())
    # Older evaluation runs carry no source field and no recorded events.
    legacy = {key: value for key, value in saved.items() if key != "source"}
    write_eval_run(tmp_path, "20261018T000000Z-live", {**legacy, "events": []})
    write_eval_run(tmp_path, "20261018T000001Z-replay", legacy, case_id="INC-006")
    replay_cli = tmp_path / "investigations" / "cli-single.json"
    replay_cli.write_text(json.dumps({**saved, "source": "replay"}))

    traces = client.get("/api/traces").json()
    assert len(traces) == 2  # console run + live evaluation run
    assert all(t["case_id"] != "INC-006" for t in traces)
    legacy_trace = next(
        t for t in traces if t["file"].startswith("20261018T000000Z-live")
    )
    assert legacy_trace["case_id"] == "INC-002"
    assert legacy_trace["correct"] is True and legacy_trace["system"] == "single"
    assert legacy_trace["model"] == "eval-model"

    for trace in traces:
        events = read_stream(client, f"/api/traces/{trace['id']}/stream?speed=1000")
        types = [e["type"] for e in events]
        assert types[0] == "console_started" and "tool_result" in types
        assert "investigation_finished" in types
    assert client.get("/api/traces/unknown/stream").status_code == 404
    assert (
        client.get(f"/api/traces/{traces[0]['id']}/stream?speed=0").status_code == 422
    )


def test_incident_buttons(setup):
    client, controller, reproduced, _ = setup
    assert client.get("/api/incidents/active").json() == {"incident": None}
    body = client.post("/api/incidents/INC-004/activate").json()
    assert body["incident"] == "INC-004" and body["observable"] is True
    assert "502" in body["symptom"] and reproduced == ["INC-004"]
    assert client.get("/api/incidents/active").json() == {"incident": "INC-004"}
    assert client.post("/api/incidents/INC-001/activate").status_code == 409
    assert client.post("/api/incidents/reset").json() == {
        "incident": None,
        "recovered": True,
    }
    assert controller.events == ["activate:INC-004", "reset"]
    assert client.post("/api/incidents/INC-999/activate").status_code == 404


def test_incident_buttons_without_controller_token(tmp_path, monkeypatch):
    monkeypatch.delenv("INCIDENT_CONTROLLER_TOKEN", raising=False)

    def missing():
        raise ControllerError("INCIDENT_CONTROLLER_TOKEN is not configured")

    app = create_app(
        Settings(save_dir=tmp_path, trace_dirs=[tmp_path]), controller_factory=missing
    )
    with local_client(app) as client:
        assert client.get("/api/options").json()["controller"] is False
        assert client.get("/api/incidents/active").status_code == 503
        assert client.post("/api/incidents/INC-001/activate").status_code == 503


def test_requests_from_other_sites_are_rejected(setup):
    client, controller, reproduced, _ = setup
    # Cross-site page: a simple POST cannot carry the custom header.
    bare = client.post(
        "/api/incidents/INC-003/activate", headers={"X-Agent-Console": ""}
    )
    assert bare.status_code == 403 and controller.events == []
    # DNS rebinding: the browser sends the attacker's host name.
    rebound = client.get("/api/options", headers={"host": "evil.example:3010"})
    assert rebound.status_code == 403
    # The page itself (GET, local host) still works without the header.
    assert (
        client.get("/api/options", headers={"X-Agent-Console": ""}).status_code == 200
    )
    assert client.get("/", headers={"host": "localhost:3010"}).status_code == 200
