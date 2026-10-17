import json
import logging
from dataclasses import dataclass
from decimal import Decimal

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from order_service.observability import metrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedProduct:
    name: str
    price: Decimal


class ProductCache:
    def __init__(self, client: Redis, ttl_seconds: int) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    def get(self, product_id: int) -> CachedProduct | None:
        try:
            value = self._client.get(self._key(product_id))
        except (RedisConnectionError, RedisTimeoutError) as error:
            metrics.record_cache_miss()
            self._log_failure("read", error)
            return None
        if value is None:
            metrics.record_cache_miss()
            return None
        try:
            data = json.loads(value)
            metrics.record_cache_hit()
            return CachedProduct(name=data["name"], price=Decimal(data["price"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning(
                "Invalid product cache entry",
                extra={"event": "redis_cache_invalid", "product_id": product_id},
                exc_info=error,
            )
            metrics.record_cache_miss()
            return None

    def set(self, product_id: int, name: str, price: Decimal) -> None:
        value = json.dumps({"name": name, "price": str(price)})
        try:
            self._client.set(self._key(product_id), value, ex=self._ttl_seconds)
        except (RedisConnectionError, RedisTimeoutError) as error:
            self._log_failure("write", error)

    @staticmethod
    def _key(product_id: int) -> str:
        return f"product:{product_id}"

    @staticmethod
    def _log_failure(operation: str, error: Exception) -> None:
        logger.error(
            "Redis cache operation failed",
            extra={"event": "redis_connection_failed", "operation": operation},
            exc_info=error,
        )
