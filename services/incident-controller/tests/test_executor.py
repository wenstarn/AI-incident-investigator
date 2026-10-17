from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from incident_controller import executor
from incident_controller.docker_control import DockerControlError
from incident_controller.executor_client import ExecutorClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DOCKER_EXECUTOR_TOKEN", "executor-test-secret")
    with TestClient(
        executor.app, headers={"X-Executor-Token": "executor-test-secret"}
    ) as client:
        yield client


def test_executor_rejects_unknown_operation_and_target(client, monkeypatch):
    control = MagicMock()
    monkeypatch.setattr(executor, "control", control)
    assert (
        client.post("/operations/exec", json={"service": "user-service"}).status_code
        == 404
    )
    assert (
        client.post("/operations/stop", json={"service": "frontend"}).status_code == 404
    )
    control.stop.assert_not_called()


def test_executor_rejects_wrong_token(client):
    response = client.post(
        "/operations/stop",
        json={"service": "user-service"},
        headers={"X-Executor-Token": "wrong"},
    )
    assert response.status_code == 403


def test_executor_hides_docker_errors(client, monkeypatch):
    control = MagicMock()
    control.stop.side_effect = DockerControlError("sensitive Docker error")
    monkeypatch.setattr(executor, "control", control)
    response = client.post("/operations/stop", json={"service": "user-service"})
    assert response.status_code == 503
    assert "sensitive" not in response.text


def test_controller_client_wraps_unavailable_executor():
    client = ExecutorClient("http://127.0.0.1:1", "secret", timeout=0.01)
    with pytest.raises(DockerControlError):
        client.stop("user-service")


def test_read_token_only_allows_read_routes(client, monkeypatch):
    monkeypatch.setenv("DOCKER_EXECUTOR_READ_TOKEN", "reader-test-secret")
    control = MagicMock()
    control.inspect_readonly.return_value = {
        "container_status": "running",
        "health_status": "healthy",
        "started_at": None,
    }
    control.logs_readonly.return_value = {
        "logs": "2026-01-01T00:00:00Z ready\n",
        "truncated": False,
        "container_status": "running",
    }
    monkeypatch.setattr(executor, "control", control)
    headers = {
        "X-Executor-Read-Token": "reader-test-secret",
        "X-Executor-Token": "reader-test-secret",
    }
    assert (
        client.get(
            "/read-only/services/order-service/status", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/read-only/services/order-service/logs?tail=2", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/operations/stop", json={"service": "user-service"}, headers=headers
        ).status_code
        == 403
    )
    control.stop.assert_not_called()
    assert (
        client.get(
            "/read-only/services/frontend/status",
            headers={"X-Executor-Read-Token": "wrong"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/read-only/services/frontend/logs?tail=201", headers=headers
        ).status_code
        == 422
    )


def test_read_token_must_differ_from_privileged_token(client, monkeypatch):
    monkeypatch.setenv("DOCKER_EXECUTOR_READ_TOKEN", "executor-test-secret")
    response = client.get(
        "/read-only/services/user-service/status",
        headers={"X-Executor-Read-Token": "executor-test-secret"},
    )
    assert response.status_code == 503
