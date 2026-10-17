from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from incident_controller.main import app, get_controller
from incident_controller.scenarios import DummyScenario
from incident_controller.service import IncidentController


@pytest.fixture
def client():
    service = IncidentController([DummyScenario()])
    app.dependency_overrides[get_controller] = lambda: service
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_catalog_health_and_initial_state(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/incidents").json() == [
        {
            "id": "dummy",
            "name": "Dummy incident",
            "description": "A test incident that does not affect the application.",
        }
    ]
    assert client.get("/incidents/active").json() == {"incident": None}


def test_activate_conflict_and_reset(client):
    activated = client.post("/incidents/dummy/activate")
    assert activated.status_code == 200
    assert activated.json()["incident"]["id"] == "dummy"
    assert client.get("/incidents/active").json() == activated.json()
    assert client.post("/incidents/dummy/activate").status_code == 409
    assert client.post("/incidents/active/reset").json() == {"incident": None}
    assert client.post("/incidents/active/reset").status_code == 200
    assert client.post("/incidents/dummy/activate").status_code == 200


def test_unknown_incident(client):
    response = client.post("/incidents/unknown/activate")
    assert response.status_code == 404
    assert client.get("/incidents/active").json() == {"incident": None}


def test_parallel_activation_allows_only_one(client):
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda _: client.post("/incidents/dummy/activate"), range(8))
        )
    assert sorted(response.status_code for response in results) == [200] + [409] * 7


def test_failed_activation_does_not_claim_active_slot():
    class FailingScenario(DummyScenario):
        def activate(self) -> None:
            raise RuntimeError("test failure")

    service = IncidentController([FailingScenario()])
    app.dependency_overrides[get_controller] = lambda: service
    try:
        with TestClient(app) as test_client:
            response = test_client.post("/incidents/dummy/activate")
            assert response.status_code == 500
            assert response.json() == {"detail": "Activation failed"}
            assert test_client.get("/incidents/active").json() == {"incident": None}
    finally:
        app.dependency_overrides.clear()


def test_failed_reset_retains_active_incident():
    class FailingResetScenario(DummyScenario):
        def reset(self) -> None:
            raise RuntimeError("test failure")

    service = IncidentController([FailingResetScenario()])
    app.dependency_overrides[get_controller] = lambda: service
    try:
        with TestClient(app) as test_client:
            assert test_client.post("/incidents/dummy/activate").status_code == 200
            response = test_client.post("/incidents/active/reset")
            assert response.status_code == 500
            assert response.json() == {"detail": "Reset failed"}
            active = test_client.get("/incidents/active").json()
            assert active["incident"]["id"] == "dummy"
    finally:
        app.dependency_overrides.clear()
