import pytest
from fastapi.testclient import TestClient

from incident_controller.fault_client import (
    FaultClientUnavailableError,
    create_fault_client,
)
from incident_controller.main import app, get_controller
from incident_controller.scenarios import RuntimeFaultScenario
from incident_controller.service import IncidentController, IncidentDependencyError


class FakeFaultClient:
    def __init__(self) -> None:
        self.faults = []
        self.closed = False

    def __enter__(self):
        self.closed = False
        return self

    def __exit__(self, *_):
        self.closed = True

    def activate_fault(self, service, fault_type, parameters, ttl_seconds=300):
        assert ttl_seconds is None
        fault = {"fault_id": "f1", "type": fault_type, "parameters": parameters}
        self.faults.append(fault)
        return fault

    def get_active_faults(self, service):
        return list(self.faults)

    def remove_fault(self, service, fault_id):
        self.faults = [fault for fault in self.faults if fault["fault_id"] != fault_id]


@pytest.mark.parametrize(
    "incident_id,service,fault_type,parameters",
    [
        (
            "INC-004",
            "order-service",
            "user_service_url_override",
            {"url": "http://user-service:9999"},
        ),
        ("INC-005", "user-service", "request_delay", {"delay_ms": 5000}),
        (
            "INC-006",
            "order-service",
            "redis_url_override",
            {"url": "redis://redis:9999/0"},
        ),
    ],
)
def test_runtime_scenario_lifecycle(incident_id, service, fault_type, parameters):
    fake = FakeFaultClient()
    scenario = RuntimeFaultScenario(
        incident_id, incident_id, "test", service, fault_type, parameters, lambda: fake
    )
    controller = IncidentController([scenario])
    assert not scenario.verify()
    assert controller.activate(incident_id) is scenario
    assert scenario.verify()
    assert fake.closed
    controller.reset()
    assert not scenario.verify()
    assert fake.faults == []


def test_runtime_scenario_dependency_error():
    def unavailable():
        raise FaultClientUnavailableError("unavailable")

    scenario = RuntimeFaultScenario(
        "INC-004",
        "test",
        "test",
        "order-service",
        "user_service_url_override",
        {"url": "http://user-service:9999"},
        unavailable,
    )
    controller = IncidentController([scenario])
    with pytest.raises(IncidentDependencyError):
        controller.activate("INC-004")


def test_missing_token_returns_actionable_503(monkeypatch):
    monkeypatch.delenv("FAULT_INJECTION_TOKEN", raising=False)
    scenario = RuntimeFaultScenario(
        "INC-004",
        "test",
        "test",
        "order-service",
        "user_service_url_override",
        {"url": "http://user-service:9999"},
        create_fault_client,
    )
    app.dependency_overrides[get_controller] = lambda: IncidentController([scenario])
    try:
        with TestClient(
            app, headers={"X-Incident-Token": "unit-test-controller-token"}
        ) as client:
            response = client.post("/incidents/INC-004/activate")
        assert response.status_code == 503
        assert response.json() == {
            "detail": "FAULT_INJECTION_TOKEN is not configured in Incident Controller"
        }
    finally:
        app.dependency_overrides.clear()
