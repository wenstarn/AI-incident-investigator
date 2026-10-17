from functools import lru_cache

from redis import Redis

from order_service.config import get_redis_timeout, get_redis_url


@lru_cache
def get_redis_client() -> Redis:
    timeout = get_redis_timeout()
    return Redis.from_url(
        get_redis_url(),
        decode_responses=True,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )


def close_redis_client() -> None:
    if get_redis_client.cache_info().currsize:
        get_redis_client().close()
        get_redis_client.cache_clear()


def check_redis() -> None:
    get_redis_client().ping()
