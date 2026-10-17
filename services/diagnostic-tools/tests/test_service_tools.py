import asyncio

import httpx
import pytest
from diagnostic_tools import (
    check_service_dependency,
    get_dependency_config,
    get_service_metrics,
    service_tools,
)
from diagnostic_tools.models import DiagnosticError


def run(coro):
    result = asyncio.run(coro)
    assert result.model_dump_json()
    return result


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("ORDER_SERVICE_URL", "http://127.0.0.1:8002")
    monkeypatch.setenv("USER_SERVICE_URL", "http://127.0.0.1:8001")
    monkeypatch.setenv("DIAGNOSTICS_TOKEN", "reader-test-token")


def test_metrics_available_and_missing(monkeypatch):
    async def available(*args, **kwargs):
        return {
            "requests_total": 5,
            "errors_total": 1,
            "cache_hits": 3,
            "secret": "hidden",
        }, None

    monkeypatch.setattr(service_tools, "_get", available)
    result = run(get_service_metrics("order-service"))
    assert result.status == "ok"
    assert result.data["metrics"] == {
        "requests_total": 5,
        "errors_total": 1,
        "cache_hits": 3,
    }

    async def missing(*args, **kwargs):
        return {}, None

    monkeypatch.setattr(service_tools, "_get", missing)
    assert run(get_service_metrics("user-service")).data["metrics"] == {}


@pytest.mark.parametrize("error_type", ["http_error", "timeout"])
def test_metrics_errors(monkeypatch, error_type):
    async def failed(*args, **kwargs):
        return None, DiagnosticError(type=error_type, message="unavailable")

    monkeypatch.setattr(service_tools, "_get", failed)
    result = run(get_service_metrics("order-service"))
    assert result.status == "error" and result.error.type == error_type


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"reachable": True, "latency_ms": 10, "status_code": 404}, "ok"),
        (
            {
                "reachable": True,
                "latency_ms": 10,
                "status_code": 500,
                "error_type": "http_error",
            },
            "degraded",
        ),
        (
            {"reachable": False, "latency_ms": 4, "error_type": "connection_refused"},
            "degraded",
        ),
        ({"reachable": False, "latency_ms": 2000, "error_type": "timeout"}, "degraded"),
    ],
)
def test_dependency_results(monkeypatch, payload, expected):
    async def observed(*args, **kwargs):
        return payload, None

    monkeypatch.setattr(service_tools, "_get", observed)
    result = run(check_service_dependency("order-service", "user-service"))
    assert result.status == expected
    if expected == "degraded":
        assert result.error.type == payload["error_type"]


def test_dependency_rejects_targets():
    assert run(check_service_dependency("user-service", "redis")).status == "error"
    assert run(check_service_dependency("order-service", "postgres")).status == "error"


def test_config_whitelists_fields(monkeypatch):
    async def observed(*args, **kwargs):
        return {
            "host": "redis",
            "port": 9999,
            "password": "secret",
            "fault_id": "hidden",
        }, None

    monkeypatch.setattr(service_tools, "_get", observed)
    result = run(get_dependency_config("order-service", "redis"))
    assert result.status == "ok"
    assert result.data == {
        "service": "order-service",
        "dependency": "redis",
        "host": "redis",
        "port": 9999,
    }
    assert "secret" not in result.model_dump_json()
    assert run(get_dependency_config("order-service", "postgres")).status == "error"


def test_config_rejects_wrong_token(monkeypatch):
    async def forbidden(*args, **kwargs):
        return None, DiagnosticError(type="forbidden", message="denied")

    monkeypatch.setattr(service_tools, "_get", forbidden)
    result = run(get_dependency_config("order-service", "redis"))
    assert result.status == "error" and result.error.type == "forbidden"


def test_diagnostics_http_rejects_external_origin(monkeypatch):
    monkeypatch.setenv("ORDER_SERVICE_URL", "http://example.com:8002")
    assert run(get_service_metrics("order-service")).error.type == "configuration_error"
    assert run(check_service_dependency("order-service", "redis")).status == "error"


def test_http_transport_timeout_and_error(monkeypatch):
    real_client = httpx.AsyncClient
    for behavior, expected in (
        (lambda request: httpx.Response(500), "http_error"),
        (
            lambda request: (_ for _ in ()).throw(
                httpx.ReadTimeout("late", request=request)
            ),
            "timeout",
        ),
    ):
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(behavior)),
        )
        result = run(get_service_metrics("order-service"))
        assert result.error.type == expected
