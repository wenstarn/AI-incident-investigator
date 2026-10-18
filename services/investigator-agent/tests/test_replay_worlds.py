"""Replay worlds must reproduce the signals recorded in docs/incident-guide.md."""

import asyncio

import pytest
from investigator_agent.evaluation.replay import WORLD_STATES, ReplayWorld


def tool(world: str, name: str, **kwargs):
    return asyncio.run(ReplayWorld.named(world).backend()[name](**kwargs))


@pytest.mark.parametrize(
    "service", ["user-service", "order-service", "postgres", "redis", "frontend"]
)
def test_healthy_world_containers_ok(service):
    assert tool("healthy", "get_service_status", service=service).status == "ok"


def test_healthy_world_checks_ok():
    assert tool("healthy", "check_postgres").status == "ok"
    assert tool("healthy", "check_redis").status == "ok"
    for dependency in ("user-service", "redis"):
        result = tool(
            "healthy",
            "check_service_dependency",
            source_service="order-service",
            dependency=dependency,
        )
        assert result.status == "ok" and result.data["reachable"] is True
    for service, path in [
        ("user-service", "/health"),
        ("user-service", "/health/db"),
        ("order-service", "/health"),
        ("order-service", "/health/db"),
        ("order-service", "/health/redis"),
    ]:
        assert (
            tool("healthy", "check_http_endpoint", service=service, path=path).status
            == "ok"
        )
    users = tool(
        "healthy", "check_http_endpoint", service="user-service", path="/users/0"
    )
    assert users.status == "degraded" and users.data["status_code"] == 404
    assert users.data["latency_ms"] < 100


def test_inc_001_user_service_stopped():
    assert (
        tool("INC-001", "get_service_status", service="user-service").data[
            "container_status"
        ]
        == "exited"
    )
    direct = tool(
        "INC-001", "check_http_endpoint", service="user-service", path="/health"
    )
    assert (
        direct.data["reachable"] is False and direct.error.type == "connection_refused"
    )
    logs = tool("INC-001", "get_service_logs", service="user-service")
    assert "Application shutdown complete" in logs.data["logs"]


def test_inc_002_redis_stopped():
    assert tool("INC-002", "get_service_status", service="redis").status == "degraded"
    assert tool("INC-002", "check_redis").error.type == "connection_refused"
    dependency = tool(
        "INC-002",
        "check_service_dependency",
        source_service="order-service",
        dependency="redis",
    )
    # Measured live: the in-service probe outlives the tool's own timeout.
    assert dependency.status == "error" and dependency.error.type == "timeout"
    health = tool(
        "INC-002", "check_http_endpoint", service="order-service", path="/health/redis"
    )
    assert health.data["status_code"] == 503 and health.data["latency_ms"] > 1000
    config = tool(
        "INC-002", "get_dependency_config", service="order-service", dependency="redis"
    )
    assert config.data["port"] == 6379
    logs = tool("INC-002", "get_service_logs", service="order-service")
    assert '"event":"redis_connection_failed"' in logs.data["logs"]
    assert '"target":"redis"' in logs.data["logs"]
    metrics = tool("INC-002", "get_service_metrics", service="order-service")
    assert metrics.data["metrics"]["redis_connection_errors_total"] == 6


def test_inc_003_postgres_stopped():
    assert (
        tool("INC-003", "get_service_status", service="postgres").status == "degraded"
    )
    assert tool("INC-003", "check_postgres").error.type == "connection_refused"
    order_db = tool(
        "INC-003",
        "check_http_endpoint",
        service="order-service",
        path="/health/db",
        timeout_seconds=15,
    )
    assert order_db.data["status_code"] == 503
    user_db = tool(
        "INC-003",
        "check_http_endpoint",
        service="user-service",
        path="/health/db",
        timeout_seconds=15,
    )
    assert user_db.data["status_code"] == 503
    for service in ("user-service", "order-service"):
        health = tool("INC-003", "check_http_endpoint", service=service, path="/health")
        assert health.status == "ok"


def test_inc_003_user_service_fails_without_its_database():
    # Measured live: User Service answers 503 to the order-service probe and a
    # direct user lookup hangs past the default timeout.
    dependency = tool(
        "INC-003",
        "check_service_dependency",
        source_service="order-service",
        dependency="user-service",
    )
    assert dependency.error.type == "http_error"
    assert dependency.data["reachable"] is True
    assert dependency.data["status_code"] == 503
    lookup = tool(
        "INC-003", "check_http_endpoint", service="user-service", path="/users/0"
    )
    assert lookup.error.type == "timeout"
    logs = tool("INC-003", "get_service_logs", service="user-service")
    assert '"operation":"get_user"' in logs.data["logs"]


def test_inc_004_wrong_user_service_port():
    assert tool("INC-004", "get_service_status", service="user-service").status == "ok"
    assert (
        tool(
            "INC-004", "check_http_endpoint", service="user-service", path="/health"
        ).status
        == "ok"
    )
    dependency = tool(
        "INC-004",
        "check_service_dependency",
        source_service="order-service",
        dependency="user-service",
    )
    assert (
        dependency.status == "degraded"
        and dependency.error.type == "connection_refused"
    )
    config = tool(
        "INC-004",
        "get_dependency_config",
        service="order-service",
        dependency="user-service",
    )
    assert config.data["port"] == 9999
    logs = tool("INC-004", "get_service_logs", service="order-service")
    assert "user-service:9999" in logs.data["logs"]


def test_inc_005_slow_user_service():
    slow = tool(
        "INC-005",
        "check_http_endpoint",
        service="user-service",
        path="/users/0",
        timeout_seconds=7,
    )
    assert slow.data["reachable"] is True and slow.data["latency_ms"] >= 4500
    default = tool(
        "INC-005", "check_http_endpoint", service="user-service", path="/users/0"
    )
    assert default.error.type == "timeout"
    dependency = tool(
        "INC-005",
        "check_service_dependency",
        source_service="order-service",
        dependency="user-service",
    )
    assert (
        dependency.error.type == "timeout"
        and 1900 < dependency.data["latency_ms"] < 2500
    )
    assert (
        tool(
            "INC-005", "check_http_endpoint", service="user-service", path="/health"
        ).status
        == "ok"
    )
    config = tool(
        "INC-005",
        "get_dependency_config",
        service="order-service",
        dependency="user-service",
    )
    assert config.data["port"] == 8001 and config.data["timeout_seconds"] == 2.0


def test_inc_006_wrong_redis_port():
    assert tool("INC-006", "check_redis").status == "ok"
    assert tool("INC-006", "get_service_status", service="redis").status == "ok"
    dependency = tool(
        "INC-006",
        "check_service_dependency",
        source_service="order-service",
        dependency="redis",
    )
    assert dependency.error.type == "connection_refused"
    assert dependency.data["latency_ms"] < 100
    config = tool(
        "INC-006", "get_dependency_config", service="order-service", dependency="redis"
    )
    assert config.data["port"] == 9999
    # Measured live: /health/redis uses the static address and stays healthy.
    health = tool(
        "INC-006", "check_http_endpoint", service="order-service", path="/health/redis"
    )
    assert health.status == "ok"
    logs = tool("INC-006", "get_service_logs", service="order-service")
    assert '"target":"redis:9999"' in logs.data["logs"]


def test_inc_001_order_logs_and_counters():
    logs = tool("INC-001", "get_service_logs", service="order-service")
    assert '"target":"user-service:8001"' in logs.data["logs"]
    assert '"exception_type":"ConnectError"' in logs.data["logs"]
    metrics = tool("INC-001", "get_service_metrics", service="order-service")
    assert metrics.data["metrics"]["dependency_errors_total"] == 3


def test_inc_005_order_logs_show_timeouts():
    logs = tool("INC-005", "get_service_logs", service="order-service")
    assert '"exception_type":"ReadTimeout"' in logs.data["logs"]
    user_logs = tool("INC-005", "get_service_logs", service="user-service")
    assert "fault_injection_applied" in user_logs.data["logs"]
    metrics = tool("INC-005", "get_service_metrics", service="order-service")
    assert metrics.data["metrics"]["dependency_timeouts_total"] == 3


def test_healthy_logs_have_no_errors():
    for service in ("order-service", "user-service"):
        logs = tool("healthy", "get_service_logs", service=service).data["logs"]
        assert '"level":"ERROR"' not in logs and '"level":"WARNING"' not in logs


def test_same_symptom_cases_differ_in_discriminating_signals():
    def snapshot(world):
        return (
            tool(world, "get_service_status", service="user-service").status,
            tool(world, "check_redis").status,
            tool(
                world,
                "get_dependency_config",
                service="order-service",
                dependency="user-service",
            ).data["port"],
            tool(
                world,
                "get_dependency_config",
                service="order-service",
                dependency="redis",
            ).data["port"],
        )

    assert snapshot("INC-001") != snapshot("INC-004")
    assert snapshot("INC-002") != snapshot("INC-006")


def test_logs_respect_tail_and_invalid_targets_error():
    logs = tool("INC-004", "get_service_logs", service="order-service", tail=2)
    assert logs.data["logs"].count("\n") == 2 and logs.data["truncated"] is True
    invalid = tool(
        "healthy", "check_http_endpoint", service="user-service", path="/health/redis"
    )
    assert invalid.status == "error" and invalid.error.type == "invalid_target"


@pytest.mark.parametrize("world", sorted(WORLD_STATES))
def test_metrics_of_stopped_service_are_unavailable(world):
    for service in ("user-service", "order-service"):
        result = tool(world, "get_service_metrics", service=service)
        if service in WORLD_STATES[world].stopped:
            assert result.status == "error"
        else:
            assert result.status == "ok" and "database_pool" in result.data["metrics"]
