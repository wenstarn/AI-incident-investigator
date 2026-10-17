from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from order_service.main import (
    app,
    get_database_health_checker,
    get_database_pool_stats,
    get_redis_health_checker,
)
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_database_health_checker] = lambda: lambda: None
    app.dependency_overrides[get_redis_health_checker] = lambda: lambda: None
    app.dependency_overrides[get_database_pool_stats] = lambda: {
        "size": 5,
        "checked_out": 2,
        "checked_in": 3,
        "overflow": 0,
    }
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "order-service"}


def test_request_id_is_generated_and_preserved(client: TestClient) -> None:
    generated = client.get("/health")
    supplied = client.get("/health", headers={"X-Request-ID": "trace-456"})

    assert generated.headers["X-Request-ID"]
    assert supplied.headers["X-Request-ID"] == "trace-456"


def test_internal_stats(client: TestClient) -> None:
    response = client.get("/internal/stats")

    assert response.status_code == 200
    assert response.json()["requests_total"] >= 1
    assert response.json()["errors_total"] >= 0
    assert response.json()["cache_hits"] >= 0
    assert response.json()["cache_misses"] >= 0
    assert response.json()["database_pool"] == {
        "size": 5,
        "checked_out": 2,
        "checked_in": 3,
        "overflow": 0,
    }


def test_cors_allows_local_frontend(client: TestClient) -> None:
    response = client.options(
        "/products",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_database_health(client: TestClient) -> None:
    response = client.get("/health/db")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_health_unavailable(client: TestClient) -> None:
    def unavailable() -> None:
        raise OperationalError("SELECT 1", {}, ConnectionError("unavailable"))

    app.dependency_overrides[get_database_health_checker] = lambda: unavailable
    response = client.get("/health/db")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_redis_health(client: TestClient) -> None:
    response = client.get("/health/redis")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_redis_health_unavailable(client: TestClient) -> None:
    def unavailable() -> None:
        raise RedisConnectionError("unavailable")

    app.dependency_overrides[get_redis_health_checker] = lambda: unavailable
    response = client.get("/health/redis")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
