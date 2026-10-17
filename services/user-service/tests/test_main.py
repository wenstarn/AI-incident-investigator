from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from user_service.main import (
    app,
    get_database_health_checker,
    get_database_pool_stats,
)
from user_service.schemas import User, UserCreate
from user_service.service import (
    EmailAlreadyExistsError,
    UserNotFoundError,
    get_user_service,
)


class FakeUserService:
    def __init__(self) -> None:
        self.users: dict[int, User] = {}

    def create_user(self, user_data: UserCreate) -> User:
        email = str(user_data.email).casefold()
        if any(str(user.email).casefold() == email for user in self.users.values()):
            raise EmailAlreadyExistsError
        user = User(id=len(self.users) + 1, name=user_data.name, email=email)
        self.users[user.id] = user
        return user

    def get_user(self, user_id: int) -> User:
        try:
            return self.users[user_id]
        except KeyError as error:
            raise UserNotFoundError from error


@pytest.fixture
def client() -> Iterator[TestClient]:
    service = FakeUserService()
    app.dependency_overrides[get_user_service] = lambda: service
    app.dependency_overrides[get_database_health_checker] = lambda: lambda: None
    app.dependency_overrides[get_database_pool_stats] = lambda: {
        "size": 5,
        "checked_out": 1,
        "checked_in": 4,
        "overflow": 0,
    }
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_request_id_is_generated_and_preserved(client: TestClient) -> None:
    generated = client.get("/health")
    supplied = client.get("/health", headers={"X-Request-ID": "trace-123"})

    assert generated.headers["X-Request-ID"]
    assert supplied.headers["X-Request-ID"] == "trace-123"


def test_internal_stats(client: TestClient) -> None:
    response = client.get("/internal/stats")

    assert response.status_code == 200
    assert response.json()["requests_total"] >= 1
    assert response.json()["errors_total"] >= 0
    assert response.json()["cache_hits"] == 0
    assert response.json()["cache_misses"] == 0
    assert response.json()["database_pool"] == {
        "size": 5,
        "checked_out": 1,
        "checked_in": 4,
        "overflow": 0,
    }


def test_cors_allows_local_frontend(client: TestClient) -> None:
    response = client.options(
        "/users/1",
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


def test_database_health_when_database_is_unavailable(client: TestClient) -> None:
    def unavailable() -> None:
        raise OperationalError("SELECT 1", {}, ConnectionError("unavailable"))

    app.dependency_overrides[get_database_health_checker] = lambda: unavailable
    response = client.get("/health/db")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_create_user(client: TestClient) -> None:
    response = client.post(
        "/users", json={"name": "Alice", "email": "alice@example.com"}
    )
    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "name": "Alice",
        "email": "alice@example.com",
    }


def test_get_user(client: TestClient) -> None:
    created_user = client.post(
        "/users", json={"name": "Alice", "email": "alice@example.com"}
    ).json()
    response = client.get(f"/users/{created_user['id']}")
    assert response.status_code == 200
    assert response.json() == created_user


def test_get_missing_user(client: TestClient) -> None:
    response = client.get("/users/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


def test_duplicate_email_is_case_insensitive(client: TestClient) -> None:
    client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
    response = client.post(
        "/users", json={"name": "Another Alice", "email": "ALICE@example.com"}
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "A user with this email already exists"}


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "email": "alice@example.com"},
        {"name": "Alice", "email": "not-an-email"},
        {"email": "alice@example.com"},
    ],
)
def test_invalid_user_returns_validation_error(
    client: TestClient, payload: dict[str, str]
) -> None:
    response = client.post("/users", json=payload)
    assert response.status_code == 422
