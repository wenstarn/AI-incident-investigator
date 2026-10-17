from io import BytesIO
from unittest.mock import MagicMock
from urllib.error import URLError

import docker
import pytest
from docker.errors import APIError, DockerException
from fastapi.testclient import TestClient

from incident_controller.docker_control import DockerControl, DockerControlError
from incident_controller.main import app, get_controller
from incident_controller.recovery import PostgresRecovery, RecoveryError
from incident_controller.scenarios import PostgresDownScenario, StopContainerScenario
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
        "INC-003",
        "INC-004",
        "INC-005",
        "INC-006",
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


@pytest.mark.parametrize("service", ["incident-controller", "order-service"])
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
        with TestClient(
            app, headers={"X-Incident-Token": "unit-test-controller-token"}
        ) as test_client:
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
        with TestClient(
            app, headers={"X-Incident-Token": "unit-test-controller-token"}
        ) as test_client:
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


def test_postgres_scenario_stops_and_starts_same_container(docker_client):
    client, container = docker_client
    container.attrs["Config"]["Labels"]["com.docker.compose.service"] = "postgres"
    recovery = MagicMock()
    scenario = PostgresDownScenario(DockerControl("test-shop"), recovery)

    scenario.activate()
    assert scenario.verify() is True
    assert (
        "com.docker.compose.service=postgres"
        in client.containers.list.call_args.kwargs["filters"]["label"]
    )
    container.stop.assert_called_once_with(timeout=10)

    scenario.reset()
    container.start.assert_called_once_with()
    recovery.wait_until_ready.assert_called_once_with("container-123")
    assert scenario.verify() is False
    assert container.id == "container-123"
    client.containers.create.assert_not_called()
    container.remove.assert_not_called()
    client.volumes.prune.assert_not_called()


def test_postgres_healthcheck_reads_actual_container_state(docker_client):
    _, container = docker_client
    container.attrs["Config"]["Labels"]["com.docker.compose.service"] = "postgres"
    container.attrs["State"] = {"Health": {"Status": "healthy"}}
    control = DockerControl("test-shop")
    assert control.health_status("postgres", expected_id="container-123") == "healthy"
    with pytest.raises(DockerControlError, match="changed"):
        control.health_status("postgres", expected_id="different")
    container.attrs["State"] = {}
    with pytest.raises(DockerControlError, match="no healthcheck"):
        control.health_status("postgres", expected_id="container-123")


def test_postgres_from_other_project_is_not_stopped(docker_client):
    _, container = docker_client
    container.attrs["Config"]["Labels"].update(
        {
            "com.docker.compose.project": "other-shop",
            "com.docker.compose.service": "postgres",
        }
    )
    with pytest.raises(DockerControlError, match="labels"):
        DockerControl("test-shop").stop("postgres")
    container.stop.assert_not_called()


def test_postgres_recovery_waits_for_healthcheck_then_both_endpoints(monkeypatch):
    control = MagicMock()
    control.health_status.side_effect = ["starting", "healthy"]
    urls = ("http://127.0.0.1:8001/health/db", "http://127.0.0.1:8002/health/db")
    requests = []

    def healthy_response(url, *, timeout):
        requests.append((url, timeout))
        response = BytesIO(b'{"status":"ok"}')
        response.status = 200
        return response

    monkeypatch.setattr("incident_controller.recovery.urlopen", healthy_response)
    monkeypatch.setattr("incident_controller.recovery.time.sleep", lambda _: None)
    recovery = PostgresRecovery(control, urls, 10, 2)
    recovery.wait_until_ready("container-123")

    assert control.health_status.call_count == 2
    assert requests == [(urls[0], 2), (urls[1], 2)]


def test_postgres_recovery_times_out_when_healthcheck_stays_unhealthy(monkeypatch):
    control = MagicMock()
    control.health_status.return_value = "starting"
    monkeypatch.setattr(
        "incident_controller.recovery.time.monotonic", iter([0, 1]).__next__
    )
    recovery = PostgresRecovery(control, ("user", "order"), 0.5, 0.1)
    with pytest.raises(RecoveryError, match="timed out"):
        recovery.wait_until_ready("container-123")


def test_postgres_recovery_requires_healthy_service_endpoints(monkeypatch):
    control = MagicMock()
    control.health_status.return_value = "healthy"
    monkeypatch.setattr(
        "incident_controller.recovery.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("unavailable")),
    )
    monkeypatch.setattr(
        "incident_controller.recovery.time.monotonic", iter([0, 0, 0, 1]).__next__
    )
    recovery = PostgresRecovery(control, ("user", "order"), 0.5, 0.1)
    with pytest.raises(RecoveryError, match="timed out"):
        recovery.wait_until_ready("container-123")


def test_postgres_reset_failure_keeps_incident_active(docker_client):
    _, container = docker_client
    container.attrs["Config"]["Labels"]["com.docker.compose.service"] = "postgres"
    recovery = MagicMock()
    recovery.wait_until_ready.side_effect = RecoveryError("healthcheck timed out")
    scenario = PostgresDownScenario(DockerControl("test-shop"), recovery)
    service = IncidentController([scenario])
    service.activate("INC-003")

    with pytest.raises(IncidentOperationError, match="reset failed"):
        service.reset()
    assert service.active_incident() is scenario
    container.start.assert_called_once_with()


def test_readonly_status_and_logs_are_bounded_and_redacted(docker_client):
    _, container = docker_client
    container.attrs["State"] = {
        "Status": "exited",
        "Health": {"Status": "unhealthy"},
        "StartedAt": "2026-01-01T00:00:00Z",
    }
    container.logs.return_value = (
        b"2026-01-01T00:00:00Z password=hidden\n"
        + b"2026-01-01T00:00:01Z safe\n" * 4000
    )
    control = DockerControl("test-shop")
    assert control.inspect_readonly("user-service")["health_status"] == "unhealthy"
    result = control.logs_readonly("user-service", tail=200, since_seconds=30)
    assert result["truncated"] is True
    assert len(result["logs"].encode()) <= 60000
    assert "password=hidden" not in result["logs"]
    assert result["logs"].startswith("2026-")
    assert container.logs.call_args.kwargs["timestamps"] is True
    container.stop.assert_not_called()
