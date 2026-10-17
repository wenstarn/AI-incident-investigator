from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from fault_injection.models import FaultCreate
from order_service import diagnostics
from order_service.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DIAGNOSTICS_TOKEN", "diagnostic-test-secret")
    monkeypatch.setenv("FAULT_INJECTION_TOKEN", "fault-test-secret")
    monkeypatch.setenv("USER_SERVICE_URL", "http://user-service:8001")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    with TestClient(app) as client:
        yield client


def headers(token="diagnostic-test-secret"):
    return {"X-Diagnostics-Token": token}


def test_diagnostics_auth_and_schema(client, monkeypatch):
    path = "/internal/diagnostics/config/redis"
    assert client.get(path).status_code == 403
    assert client.get(path, headers=headers("fault-test-secret")).status_code == 403
    assert client.get(path, headers=headers()).status_code == 200
    assert path not in client.get("/openapi.json").json()["paths"]
    assert client.post(path, headers=headers()).status_code == 405
    assert (
        client.get(
            "/internal/diagnostics/config/postgres", headers=headers()
        ).status_code
        == 404
    )
    monkeypatch.delenv("DIAGNOSTICS_TOKEN")
    assert client.get(path, headers=headers()).status_code == 503


def test_effective_config_hides_credentials_and_override(client, monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://reader:password@redis:6379/0")
    response = client.get("/internal/diagnostics/config/redis", headers=headers())
    assert response.status_code == 200
    assert response.json()["host"] == "redis"
    assert "password" not in response.text
    fault = app.state.fault_registry.set_fault(
        FaultCreate(
            type="redis_url_override", parameters={"url": "redis://redis:9999/0"}
        )
    )
    try:
        response = client.get("/internal/diagnostics/config/redis", headers=headers())
        assert response.json()["port"] == 9999
    finally:
        app.state.fault_registry.remove_fault(fault.fault_id)
    assert (
        client.get("/internal/diagnostics/config/redis", headers=headers()).json()[
            "port"
        ]
        == 6379
    )


def test_user_dependency_probe_uses_effective_url(client, monkeypatch):
    observed = []
    fake = MagicMock()
    fake.__enter__.return_value = fake
    fake.get.return_value = httpx.Response(404)

    def factory(**kwargs):
        observed.append(kwargs["base_url"])
        return fake

    monkeypatch.setattr(diagnostics.httpx, "Client", factory)
    response = client.get(
        "/internal/diagnostics/dependencies/user-service", headers=headers()
    )
    assert response.status_code == 200
    assert response.json()["reachable"] is True
    assert response.json()["status_code"] == 404
    fault = app.state.fault_registry.set_fault(
        FaultCreate(
            type="user_service_url_override",
            parameters={"url": "http://user-service:9999"},
        )
    )
    try:
        client.get("/internal/diagnostics/dependencies/user-service", headers=headers())
        assert observed[-1] == "http://user-service:9999"
    finally:
        app.state.fault_registry.remove_fault(fault.fault_id)
    fake.get.assert_called_with("/users/0")


def test_redis_dependency_probe_uses_ping_only(client, monkeypatch):
    fake = MagicMock()
    fake.ping.return_value = True
    monkeypatch.setattr(diagnostics, "create_redis_client", lambda url: fake)
    response = client.get("/internal/diagnostics/dependencies/redis", headers=headers())
    assert response.json()["reachable"] is True
    fake.ping.assert_called_once()
    fake.set.assert_not_called()
    fake.get.assert_not_called()


def test_diagnostic_token_cannot_manage_faults(client):
    response = client.get(
        "/internal/faults", headers={"X-Fault-Token": "diagnostic-test-secret"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectError("refused"), "connection_refused"),
        (httpx.ReadTimeout("late"), "timeout"),
    ],
)
def test_user_dependency_transport_failures(client, monkeypatch, failure, expected):
    fake = MagicMock()
    fake.__enter__.return_value = fake
    fake.get.side_effect = failure
    monkeypatch.setattr(diagnostics.httpx, "Client", lambda **kwargs: fake)
    response = client.get(
        "/internal/diagnostics/dependencies/user-service", headers=headers()
    )
    assert response.status_code == 200
    assert response.json()["reachable"] is False
    assert response.json()["error_type"] == expected


def test_equal_fault_and_diagnostics_tokens_fail_closed(client, monkeypatch):
    monkeypatch.setenv("DIAGNOSTICS_TOKEN", "fault-test-secret")
    assert (
        client.get(
            "/internal/diagnostics/config/redis", headers=headers("fault-test-secret")
        ).status_code
        == 503
    )
    assert (
        client.get(
            "/internal/faults", headers={"X-Fault-Token": "fault-test-secret"}
        ).status_code
        == 503
    )
