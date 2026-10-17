import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from diagnostic_tools import (
    check_http_endpoint,
    check_postgres,
    check_redis,
    docker_tools,
    get_service_logs,
    get_service_status,
    postgres_tools,
    redis_tools,
)
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


def run(coro):
    result = asyncio.run(coro)
    assert result.model_dump_json()
    return result


@pytest.mark.parametrize(
    ("state", "health", "expected"),
    [
        ("running", "healthy", "ok"),
        ("exited", None, "degraded"),
        ("running", "unhealthy", "degraded"),
    ],
)
def test_status(monkeypatch, state, health, expected):
    async def read(*args, **kwargs):
        return {
            "container_status": state,
            "health_status": health,
            "started_at": None,
        }, None

    monkeypatch.setattr(docker_tools, "_read", read)
    assert run(get_service_status("user-service")).status == expected


@pytest.mark.parametrize("kind", ["container_not_found", "connection_refused"])
def test_status_errors(monkeypatch, kind):
    from diagnostic_tools.models import DiagnosticError

    async def read(*args, **kwargs):
        return None, DiagnosticError(type=kind, message="unavailable")

    monkeypatch.setattr(docker_tools, "_read", read)
    result = run(get_service_status("user-service"))
    assert result.status == "error" and result.error.type == kind


def test_logs(monkeypatch):
    async def read(*args, **kwargs):
        return {
            "logs": "2026-01-01T00:00:00Z sample\n",
            "truncated": False,
            "container_status": "exited",
        }, None

    monkeypatch.setattr(docker_tools, "_read", read)
    result = run(get_service_logs("redis"))
    assert result.data["logs"].startswith("2026-")
    assert result.data["container_status"] == "exited"
    assert run(get_service_logs("redis", tail=0)).status == "error"
    assert run(get_service_logs("redis", since_seconds=-1)).status == "error"


@pytest.mark.parametrize("code,expected", [(200, "ok"), (500, "degraded")])
def test_http(monkeypatch, code, expected):
    def handler(request):
        return httpx.Response(code, text="ok")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    assert run(check_http_endpoint("user-service", "/health")).status == expected


def test_http_errors(monkeypatch):
    assert run(check_http_endpoint("unknown", "/health")).status == "error"
    assert run(check_http_endpoint("user-service", "/users")).status == "error"
    real_client = httpx.AsyncClient
    for exc in (httpx.ConnectError("refused"), httpx.ReadTimeout("timeout")):

        def handler(request):
            raise exc

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
        )
        assert run(check_http_endpoint("user-service", "/health")).status == "degraded"


def test_postgres(monkeypatch):
    monkeypatch.setenv("DIAGNOSTIC_POSTGRES_PASSWORD", "test")
    cursor = AsyncMock()
    cursor.fetchone.return_value = (1,)
    cursor.__aenter__.return_value = cursor
    connection = AsyncMock()
    connection.cursor = MagicMock(return_value=cursor)
    connection.__aenter__.return_value = connection
    monkeypatch.setattr(
        postgres_tools.psycopg.AsyncConnection,
        "connect",
        AsyncMock(return_value=connection),
    )
    assert run(check_postgres()).status == "ok"


@pytest.mark.parametrize("exception", [ConnectionRefusedError(), TimeoutError()])
def test_postgres_errors(monkeypatch, exception):
    monkeypatch.setenv("DIAGNOSTIC_POSTGRES_PASSWORD", "test")
    monkeypatch.setattr(
        postgres_tools.psycopg.AsyncConnection,
        "connect",
        AsyncMock(side_effect=exception),
    )
    assert run(check_postgres()).status == "degraded"


@pytest.mark.parametrize(
    "exception,expected",
    [
        (None, "ok"),
        (RedisConnectionError(), "degraded"),
        (RedisTimeoutError(), "degraded"),
    ],
)
def test_redis(monkeypatch, exception, expected):
    client = AsyncMock()
    client.ping.side_effect = exception
    client.ping.return_value = True
    monkeypatch.setattr(redis_tools.redis, "Redis", lambda **kwargs: client)
    assert run(check_redis()).status == expected


def test_logs_empty_and_truncated(monkeypatch):
    async def read(*args, **kwargs):
        return {"logs": "", "truncated": True, "container_status": "exited"}, None

    monkeypatch.setattr(docker_tools, "_read", read)
    result = run(get_service_logs("user-service", tail=200, since_seconds=30))
    assert result.status == "ok"
    assert result.data["logs"] == ""
    assert result.data["truncated"] is True


def test_postgres_authentication_failure(monkeypatch):
    import psycopg

    monkeypatch.setenv("DIAGNOSTIC_POSTGRES_PASSWORD", "test")
    monkeypatch.setattr(
        postgres_tools.psycopg.AsyncConnection,
        "connect",
        AsyncMock(
            side_effect=psycopg.errors.InvalidPassword("password authentication failed")
        ),
    )
    result = run(check_postgres())
    assert result.status == "degraded"
    assert result.error.type == "authentication_failed"


def test_http_user_lookup_can_use_bounded_longer_timeout(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(404))
        ),
    )
    result = run(check_http_endpoint("user-service", "/users/0", timeout_seconds=7))
    assert result.status == "degraded"
    assert result.data["reachable"] is True
    assert result.data["status_code"] == 404
    assert (
        run(check_http_endpoint("user-service", "/users/0", timeout_seconds=0)).status
        == "error"
    )
