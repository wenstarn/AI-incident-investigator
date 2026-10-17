import json
from decimal import Decimal
from unittest.mock import Mock

import pytest
from order_service.cache import ProductCache
from order_service.models import ProductModel
from order_service.service import OrderService
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


class ExistingUserClient:
    def ensure_user_exists(self, user_id: int) -> None:
        pass


def make_service(redis_client: Mock, product: ProductModel) -> OrderService:
    service = OrderService(
        Mock(), ExistingUserClient(), ProductCache(redis_client, ttl_seconds=300)
    )
    service._repository = Mock()  # type: ignore[assignment]
    service._repository.get_product.return_value = product
    return service


def product(stock: int = 7) -> ProductModel:
    return ProductModel(
        id=42, name="Database name", price=Decimal("19.99"), stock=stock
    )


def test_cache_miss_reads_database_and_writes_with_ttl() -> None:
    redis_client = Mock()
    redis_client.get.return_value = None
    service = make_service(redis_client, product())

    result = service.get_product(42)

    service._repository.get_product.assert_called_once_with(42)
    redis_client.get.assert_called_once_with("product:42")
    redis_client.set.assert_called_once_with(
        "product:42",
        json.dumps({"name": "Database name", "price": "19.99"}),
        ex=300,
    )
    assert result.model_dump() == {
        "id": 42,
        "name": "Database name",
        "price": Decimal("19.99"),
        "stock": 7,
    }


def test_cache_hit_uses_cached_fields_but_database_stock() -> None:
    redis_client = Mock()
    redis_client.get.return_value = json.dumps(
        {"name": "Cached name", "price": "12.50"}
    )
    service = make_service(redis_client, product(stock=3))

    result = service.get_product(42)

    service._repository.get_product.assert_called_once_with(42)
    redis_client.set.assert_not_called()
    assert result.name == "Cached name"
    assert result.price == Decimal("12.50")
    assert result.stock == 3


@pytest.mark.parametrize("error", [RedisConnectionError(), RedisTimeoutError()])
def test_redis_read_error_falls_back_to_database(error: Exception) -> None:
    redis_client = Mock()
    redis_client.get.side_effect = error
    service = make_service(redis_client, product())

    result = service.get_product(42)

    assert result.name == "Database name"
    assert result.price == Decimal("19.99")
    assert result.stock == 7


def test_redis_override_failure_has_structured_target(monkeypatch) -> None:
    from order_service import cache as cache_module

    recorded = Mock()
    monkeypatch.setattr(cache_module.logger, "error", recorded)
    redis_client = Mock()
    redis_client.get.side_effect = RedisConnectionError("unavailable")
    cache = ProductCache(redis_client, ttl_seconds=300, target="redis:9999")

    assert cache.get(42) is None
    assert recorded.call_args.kwargs["extra"] == {
        "event": "redis_connection_failed",
        "operation": "read",
        "target": "redis:9999",
    }


def test_redis_write_error_does_not_fail_request() -> None:
    redis_client = Mock()
    redis_client.get.return_value = None
    redis_client.set.side_effect = RedisConnectionError()
    service = make_service(redis_client, product())

    result = service.get_product(42)

    assert result.id == 42
    assert result.stock == 7
