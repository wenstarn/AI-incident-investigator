import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import order_service.service as order_service_module
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from fault_injection.models import FaultCreate
from order_service.config import DEFAULT_DATABASE_SCHEMA
from order_service.database import get_session
from order_service.main import app, get_database_health_checker
from order_service.models import OrderModel, ProductModel
from order_service.schemas import OrderCreate
from order_service.service import InsufficientStockError, OrderService
from order_service.user_client import UserServiceClient, get_user_service_client
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import DropSchema


class ExistingUserClient:
    def ensure_user_exists(self, user_id: int) -> None:
        pass


@pytest.fixture(scope="module")
def database() -> Iterator[tuple[str, str]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")

    schema = f"test_order_service_{uuid4().hex}"
    service_root = Path(__file__).parents[1]
    config = Config(service_root / "alembic.ini")
    old_schema = os.environ.get("ORDER_SERVICE_DB_SCHEMA")
    old_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    os.environ["ORDER_SERVICE_DB_SCHEMA"] = schema
    command.upgrade(config, "head")
    try:
        yield database_url, schema
    finally:
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
        _restore_environment("ORDER_SERVICE_DB_SCHEMA", old_schema)
        _restore_environment("DATABASE_URL", old_database_url)


def _restore_environment(name: str, old_value: str | None) -> None:
    if old_value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = old_value


@pytest.fixture
def session_factory(
    database: tuple[str, str],
) -> Iterator[sessionmaker[Session]]:
    database_url, schema = database
    engine = create_engine(
        database_url,
        pool_size=10,
        max_overflow=10,
        execution_options={"schema_translate_map": {DEFAULT_DATABASE_SCHEMA: schema}},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f'TRUNCATE TABLE "{schema}".orders, "{schema}".products '
            "RESTART IDENTITY CASCADE"
        )
    yield factory
    engine.dispose()


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    class TestCache:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def set(self, key: str, value: str, ex: int) -> None:
            self.values[key] = value

    monkeypatch.setattr(order_service_module, "get_redis_client", TestCache)

    def session_dependency() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    engine = session_factory.kw["bind"]

    def health_check() -> None:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_database_health_checker] = lambda: health_check
    app.dependency_overrides[get_user_service_client] = lambda: ExistingUserClient()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def add_product(factory: sessionmaker[Session], stock: int = 10) -> ProductModel:
    with factory() as session:
        product = ProductModel(name="Test product", price=Decimal("19.99"), stock=stock)
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@pytest.mark.integration
def test_redis_url_fault_falls_back_and_resets(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    product = add_product(session_factory)
    fault = app.state.fault_registry.set_fault(
        FaultCreate(
            type="redis_url_override",
            parameters={"url": "redis://127.0.0.1:9999/0"},
        )
    )
    try:
        response = client.get(f"/products/{product.id}")
        assert response.status_code == 200
        assert response.json()["stock"] == product.stock
    finally:
        app.state.fault_registry.remove_fault(fault.fault_id)
    assert client.get(f"/products/{product.id}").status_code == 200


@pytest.mark.integration
def test_product_endpoints_and_pagination(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    first = add_product(session_factory)
    with session_factory() as session:
        session.add(ProductModel(name="Second", price=Decimal("5.50"), stock=4))
        session.commit()

    page = client.get("/products", params={"limit": 1, "offset": 1})
    product = client.get(f"/products/{first.id}")
    missing = client.get("/products/9999")

    assert page.status_code == 200
    assert len(page.json()) == 1
    assert page.json()[0]["name"] == "Second"
    assert product.status_code == 200
    assert product.json()["price"] == "19.99"
    assert missing.status_code == 404


@pytest.mark.integration
def test_create_get_filter_and_sort_orders(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    product = add_product(session_factory)
    first = client.post(
        "/orders",
        json={"user_id": 7, "product_id": product.id, "quantity": 2},
    )
    second = client.post(
        "/orders",
        json={"user_id": 7, "product_id": product.id, "quantity": 3},
    )
    other = client.post(
        "/orders",
        json={"user_id": 8, "product_id": product.id, "quantity": 1},
    )
    fetched = client.get(f"/orders/{first.json()['id']}")
    filtered = client.get(
        "/orders", params={"user_id": 7, "sort_order": "desc", "limit": 1}
    )

    assert first.status_code == second.status_code == other.status_code == 201
    assert fetched.json() == first.json()
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [second.json()["id"]]
    assert client.get("/orders/9999").status_code == 404
    assert (
        client.post(
            "/orders",
            json={"user_id": 1, "product_id": product.id, "quantity": 100},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/orders", json={"user_id": 1, "product_id": 9999, "quantity": 1}
        ).status_code
        == 404
    )


@pytest.mark.integration
def test_concurrent_orders_never_make_stock_negative(
    session_factory: sessionmaker[Session],
) -> None:
    product = add_product(session_factory, stock=2)

    def create_one_order(user_id: int) -> bool:
        with session_factory() as session:
            try:
                OrderService(session, ExistingUserClient()).create_order(
                    OrderCreate(user_id=user_id, product_id=product.id, quantity=1)
                )
            except InsufficientStockError:
                return False
            return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create_one_order, range(1, 9)))

    with session_factory() as session:
        remaining_stock = session.get(ProductModel, product.id).stock
        order_count = session.scalar(
            select(func.count())
            .select_from(OrderModel)
            .where(OrderModel.product_id == product.id)
        )

    assert results.count(True) == 2
    assert results.count(False) == 6
    assert remaining_stock == 0
    assert order_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    ("behavior", "expected_status"),
    [
        ("missing", 404),
        ("unavailable", 502),
        ("timeout", 504),
        ("unexpected", 502),
    ],
)
def test_user_service_failure_does_not_change_database(
    client: TestClient,
    session_factory: sessionmaker[Session],
    behavior: str,
    expected_status: int,
) -> None:
    product = add_product(session_factory, stock=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if behavior == "missing":
            return httpx.Response(404, request=request)
        if behavior == "unavailable":
            raise httpx.ConnectError("connection failed", request=request)
        if behavior == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(500, request=request)

    http_client = httpx.Client(
        base_url="http://user-service.test",
        transport=httpx.MockTransport(handler),
    )
    app.dependency_overrides[get_user_service_client] = lambda: UserServiceClient(
        http_client
    )

    response = client.post(
        "/orders",
        json={"user_id": 42, "product_id": product.id, "quantity": 1},
    )

    with session_factory() as session:
        stock = session.get(ProductModel, product.id).stock
        order_count = session.scalar(select(func.count()).select_from(OrderModel))
    http_client.close()

    assert response.status_code == expected_status
    assert stock == 5
    assert order_count == 0
