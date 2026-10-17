from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from fault_injection.models import FaultCreate
from fault_injection.registry import (
    FaultConflictError,
    FaultRegistry,
    FaultValidationError,
)
from order_service.main import app as order_app
from user_service.main import app as user_app


def test_registry_lifecycle_and_expiry() -> None:
    now = [100.0]
    registry = FaultRegistry({"request_delay"}, clock=lambda: now[0])
    other = FaultRegistry({"request_delay"}, clock=lambda: now[0])
    data = FaultCreate(type="request_delay", parameters={"delay_ms": 5}, ttl_seconds=1)
    fault = registry.set_fault(data)
    assert registry.get_fault(fault.fault_id) == fault
    assert other.get_active_faults() == []
    with pytest.raises(FaultConflictError):
        registry.set_fault(data)
    now[0] += 1
    assert registry.get_fault(fault.fault_id) is None
    assert len(registry.get_active_faults()) == 0
    next_fault = registry.set_fault(data)
    registry.remove_fault(next_fault.fault_id)
    assert registry.get_active_faults() == []
    registry.set_fault(data)
    registry.clear_all()
    assert registry.get_active_faults() == []


def test_registry_concurrent_activation() -> None:
    registry = FaultRegistry({"request_delay"})
    data = FaultCreate(type="request_delay", parameters={"delay_ms": 0})

    def attempt(_: int) -> bool:
        try:
            registry.set_fault(data)
            return True
        except FaultConflictError:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        assert sum(pool.map(attempt, range(50))) == 1


def test_allowed_types_and_url_parameters() -> None:
    user = FaultRegistry({"request_delay"})
    with pytest.raises(FaultValidationError):
        user.set_fault(
            FaultCreate(
                type="redis_url_override", parameters={"url": "redis://redis:6379/0"}
            )
        )
    order = FaultRegistry({"user_service_url_override", "redis_url_override"})
    for fault_type, url in (
        ("user_service_url_override", "http://user-service:9999"),
        ("redis_url_override", "redis://redis:9999/0"),
    ):
        assert (
            order.set_fault(FaultCreate(type=fault_type, parameters={"url": url})).type
            == fault_type
        )
    for fault_type, url in (
        ("user_service_url_override", "http://example.com:8001"),
        ("redis_url_override", "redis://redis:1234/0"),
    ):
        with pytest.raises(FaultValidationError):
            order.set_fault(FaultCreate(type=fault_type, parameters={"url": url}))


@pytest.mark.parametrize("app", [user_app, order_app])
def test_fault_api(app, monkeypatch) -> None:
    monkeypatch.setenv("FAULT_INJECTION_TOKEN", "test-only-token")
    app.state.fault_registry.clear_all()
    with TestClient(app) as client:
        path = "/internal/faults"
        payload = {"type": "request_delay", "parameters": {"delay_ms": 10}}
        assert client.post(path, json=payload).status_code == 403
        assert (
            client.post(
                path, json=payload, headers={"X-Fault-Token": "bad"}
            ).status_code
            == 403
        )
        headers = {"X-Fault-Token": "test-only-token"}
        created = client.post(path, json=payload, headers=headers)
        assert created.status_code == 201
        fault_id = created.json()["fault_id"]
        assert client.post(path, json=payload, headers=headers).status_code == 409
        assert client.get(path, headers=headers).json()[0]["fault_id"] == fault_id
        assert client.get(f"{path}/{fault_id}", headers=headers).status_code == 200
        for bad in (
            {"type": "unknown", "parameters": {}},
            {"type": "request_delay", "parameters": {"delay_ms": -1}},
            {**payload, "ttl_seconds": 1801},
        ):
            assert client.post(path, json=bad, headers=headers).status_code == 422
        assert client.delete(f"{path}/{fault_id}", headers=headers).status_code == 204
        assert client.delete(f"{path}/{fault_id}", headers=headers).status_code == 204
        assert client.post(path, json=payload, headers=headers).status_code == 201
        assert client.delete(path, headers=headers).status_code == 204
        assert client.get(path, headers=headers).json() == []
        assert path not in client.get("/openapi.json").json()["paths"]


def test_disabled_api(monkeypatch) -> None:
    monkeypatch.delenv("FAULT_INJECTION_TOKEN", raising=False)
    with TestClient(user_app) as client:
        assert (
            client.get("/internal/faults", headers={"X-Fault-Token": "any"}).status_code
            == 403
        )
        assert client.get("/health").status_code == 200


def test_fault_without_ttl_remains_until_explicit_removal() -> None:
    now = [100.0]
    registry = FaultRegistry({"request_delay"}, clock=lambda: now[0])
    request = FaultCreate(
        type="request_delay", parameters={"delay_ms": 5000}, ttl_seconds=None
    )
    fault = registry.set_fault(request)
    assert fault.expires_at is None
    now[0] += 86400
    assert registry.get_fault_by_type("request_delay") == fault
    with pytest.raises(FaultConflictError):
        registry.set_fault(request)
    registry.remove_fault(fault.fault_id)
    assert registry.get_active_faults() == []
