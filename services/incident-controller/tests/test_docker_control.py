from unittest.mock import MagicMock

import docker
import pytest
from docker.errors import APIError, DockerException
from fastapi.testclient import TestClient

from incident_controller.docker_control import DockerControl, DockerControlError
from incident_controller.main import app, get_controller
from incident_controller.scenarios import StopContainerScenario
from incident_controller.service import (
    IncidentController,
    IncidentOperationError,
    controller,
)


@pytest.fixture
def docker_client(monkeypatch):
    client = MagicMock()
    container = MagicMock()
    container.id = "container-123"
    container.status = "running"
    container.attrs = {
        "Config": {
            "Labels": {
                "com.docker.compose.project": "test-shop",
                "com.docker.compose.service": "user-service",
            }
        }
    }
    container.stop.side_effect = lambda **_: setattr(container, "status", "exited")
    container.start.side_effect = lambda: setattr(container, "status", "running")
    client.containers.list.return_value = [container]
    monkeypatch.setattr(docker, "from_env", lambda: client)
    return client, container


def test_stop_and_start_use_compose_labels_and_verify_real_status(docker_client):
    client, container = docker_client
    control = DockerControl("test-shop")
    scenario = StopContainerScenario(
        "INC-001", "User", "Stop User", "user-service", control
    )

    scenario.activate()
    client.containers.list.assert_called_with(
        all=True,
        filters={
            "label": [
                "com.docker.compose.project=test-shop",
                "com.docker.compose.service=user-service",
            ]
        },
    )
    container.stop.assert_called_once_with(timeout=10)
    assert scenario.verify() is True

    scenario.reset()
    container.start.assert_called_once_with()
    assert scenario.verify() is False
    assert container.reload.call_count >= 4


def test_default_catalog_contains_requested_scenarios():
    assert {item.id for item in controller.list_incidents()} == {
        "dummy",
        "INC-001",
        "INC-002",
    }


def test_redis_scenario_targets_only_redis(docker_client):
    client, container = docker_client
    container.attrs["Config"]["Labels"]["com.docker.compose.service"] = "redis"
    scenario = StopContainerScenario(
        "INC-002", "Redis", "Stop Redis", "redis", DockerControl("test-shop")
    )
    scenario.activate()
    assert scenario.verify() is True
    assert (
        "com.docker.compose.service=redis"
        in client.containers.list.call_args.kwargs["filters"]["label"]
    )


@pytest.mark.parametrize(
    "service", ["incident-controller", "postgres", "order-service"]
)
def test_disallowed_service_is_never_queried_or_stopped(docker_client, service):
    client, container = docker_client
    with pytest.raises(DockerControlError, match="not allowed"):
        DockerControl("test-shop").stop(service)
    client.containers.list.assert_not_called()
    container.stop.assert_not_called()


def test_cross_project_container_is_rejected_even_if_sdk_returns_it(docker_client):
    _, container = docker_client
    container.attrs["Config"]["Labels"]["com.docker.compose.project"] = "other"
    with pytest.raises(DockerControlError, match="labels"):
        DockerControl("test-shop").stop("user-service")
    container.stop.assert_not_called()


def test_ambiguous_target_is_rejected(docker_client):
    client, container = docker_client
    client.containers.list.return_value = [container, container]
    with pytest.raises(DockerControlError, match="exactly one"):
        DockerControl("test-shop").stop("user-service")
    container.stop.assert_not_called()


def test_reset_rejects_replaced_container(docker_client):
    _, container = docker_client
    scenario = StopContainerScenario(
        "INC-001", "User", "Stop User", "user-service", DockerControl("test-shop")
    )
    scenario.activate()
    container.id = "replacement"
    with pytest.raises(DockerControlError, match="changed"):
        scenario.reset()
    container.start.assert_not_called()


def test_docker_start_error_keeps_incident_active(docker_client):
    _, container = docker_client
    scenario = StopContainerScenario(
        "INC-001", "User", "Stop User", "user-service", DockerControl("test-shop")
    )
    service = IncidentController([scenario])
    service.activate("INC-001")
    container.start.side_effect = APIError("start failed")
    with pytest.raises(IncidentOperationError, match="reset failed"):
        service.reset()
    assert service.active_incident() is scenario


def test_reset_docker_api_error_returns_503(docker_client):
    _, container = docker_client
    scenario = StopContainerScenario(
        "INC-001", "User", "Stop User", "user-service", DockerControl("test-shop")
    )
    service = IncidentController([scenario])
    service.activate("INC-001")
    container.start.side_effect = APIError("start failed")
    app.dependency_overrides[get_controller] = lambda: service
    try:
        with TestClient(app) as test_client:
            response = test_client.post("/incidents/active/reset")
            assert response.status_code == 503
            assert response.json() == {"detail": "Docker operation failed"}
            active = test_client.get("/incidents/active").json()
            assert active["incident"]["id"] == "INC-001"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "error", [DockerException("socket error"), APIError("API error")]
)
def test_docker_errors_are_wrapped_and_not_exposed(docker_client, error):
    client, _ = docker_client
    client.containers.list.side_effect = error
    scenario = StopContainerScenario(
        "INC-001", "User", "Stop User", "user-service", DockerControl("test-shop")
    )
    service = IncidentController([scenario])
    app.dependency_overrides[get_controller] = lambda: service
    try:
        with TestClient(app) as test_client:
            response = test_client.post("/incidents/INC-001/activate")
            assert response.status_code == 503
            assert response.json() == {"detail": "Docker operation failed"}
            assert test_client.get("/incidents/active").json() == {"incident": None}
    finally:
        app.dependency_overrides.clear()


def test_stopped_container_cannot_be_claimed_or_started(docker_client):
    _, container = docker_client
    container.status = "exited"
    scenario = StopContainerScenario(
        "INC-001", "User", "Stop User", "user-service", DockerControl("test-shop")
    )
    service = IncidentController([scenario])
    with pytest.raises(IncidentOperationError, match="activation failed"):
        service.activate("INC-001")
    container.stop.assert_not_called()
    container.start.assert_not_called()
