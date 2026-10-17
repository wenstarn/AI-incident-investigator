"""Direct Redis PING probe."""

import time

from redis import asyncio as redis
from redis.exceptions import RedisError

from diagnostic_tools.config import DiagnosticConfig
from diagnostic_tools.errors import connection_error
from diagnostic_tools.models import DiagnosticResult


async def check_redis() -> DiagnosticResult:
    config = DiagnosticConfig.from_env()
    start = time.monotonic()
    client = redis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        socket_connect_timeout=config.redis_timeout_seconds,
        socket_timeout=config.redis_timeout_seconds,
    )
    try:
        pong = await client.ping()
    except (RedisError, OSError, TimeoutError) as error:
        return DiagnosticResult(
            tool="check_redis",
            status="degraded",
            data={
                "reachable": False,
                "ping": False,
                "latency_ms": round((time.monotonic() - start) * 1000, 2),
            },
            error=connection_error(error),
        )
    finally:
        await client.aclose()
    return DiagnosticResult(
        tool="check_redis",
        status="ok" if pong else "degraded",
        data={
            "reachable": True,
            "ping": bool(pong),
            "latency_ms": round((time.monotonic() - start) * 1000, 2),
        },
    )
