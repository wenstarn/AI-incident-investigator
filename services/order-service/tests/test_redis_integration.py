import os
from decimal import Decimal
from uuid import uuid4

import pytest
from order_service.cache import ProductCache
from redis import Redis


@pytest.mark.integration
def test_real_redis_cache_miss_then_hit() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("TEST_REDIS_URL is required for Redis integration tests")

    client = Redis.from_url(redis_url, decode_responses=True)
    product_id = int(uuid4().int % 1_000_000_000) + 1_000_000_000
    key = f"product:{product_id}"
    cache = ProductCache(client, ttl_seconds=30)
    try:
        client.delete(key)
        assert cache.get(product_id) is None

        cache.set(product_id, "Integration product", Decimal("12.34"))
        cached = cache.get(product_id)

        assert cached is not None
        assert cached.name == "Integration product"
        assert cached.price == Decimal("12.34")
        assert 0 < client.ttl(key) <= 30
    finally:
        client.delete(key)
        client.close()
