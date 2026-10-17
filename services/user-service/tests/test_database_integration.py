import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import DropSchema
from user_service.config import DEFAULT_DATABASE_SCHEMA
from user_service.database import get_session
from user_service.main import app, get_database_health_checker


@pytest.fixture(scope="module")
def database() -> Iterator[tuple[str, str]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")

    schema = f"test_user_service_{uuid4().hex}"
    service_root = Path(__file__).parents[1]
    config = Config(service_root / "alembic.ini")
    old_schema = os.environ.get("USER_SERVICE_DB_SCHEMA")
    old_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    os.environ["USER_SERVICE_DB_SCHEMA"] = schema

    command.upgrade(config, "head")
    try:
        yield database_url, schema
    finally:
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
        _restore_environment("USER_SERVICE_DB_SCHEMA", old_schema)
        _restore_environment("DATABASE_URL", old_database_url)


def _restore_environment(name: str, old_value: str | None) -> None:
    if old_value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = old_value


@pytest.fixture
def database_client(database: tuple[str, str]) -> Iterator[TestClient]:
    database_url, schema = database
    engine = create_engine(
        database_url,
        execution_options={"schema_translate_map": {DEFAULT_DATABASE_SCHEMA: schema}},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_dependency() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def health_check() -> None:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_database_health_checker] = lambda: health_check
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.mark.integration
def test_migration_created_users_table(database: tuple[str, str]) -> None:
    database_url, schema = database
    engine = create_engine(database_url)
    try:
        tables = inspect(engine).get_table_names(schema=schema)
        assert "users" in tables
        assert "alembic_version" in tables
    finally:
        engine.dispose()


@pytest.mark.integration
def test_users_persist_between_application_clients(
    database_client: TestClient,
    database: tuple[str, str],
) -> None:
    email = f"persistent-{uuid4().hex}@example.com"
    created = database_client.post(
        "/users", json={"name": "Persistent User", "email": email}
    )
    assert created.status_code == 201

    database_client.close()
    database_url, schema = database
    engine = create_engine(
        database_url,
        execution_options={"schema_translate_map": {DEFAULT_DATABASE_SCHEMA: schema}},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def new_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = new_session
    with TestClient(app) as restarted_client:
        fetched = restarted_client.get(f"/users/{created.json()['id']}")

    engine.dispose()
    assert fetched.status_code == 200
    assert fetched.json() == created.json()


@pytest.mark.integration
def test_real_database_duplicate_and_health(database_client: TestClient) -> None:
    email = f"duplicate-{uuid4().hex}@example.com"
    first = database_client.post("/users", json={"name": "First", "email": email})
    duplicate = database_client.post(
        "/users", json={"name": "Second", "email": email.upper()}
    )
    health = database_client.get("/health/db")

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert health.status_code == 200
