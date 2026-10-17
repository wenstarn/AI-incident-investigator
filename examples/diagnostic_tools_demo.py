"""Print JSON output from all eight diagnostic tools."""

import asyncio
import os
from pathlib import Path

from diagnostic_tools import (
    check_http_endpoint,
    check_postgres,
    check_redis,
    check_service_dependency,
    get_dependency_config,
    get_service_logs,
    get_service_metrics,
    get_service_status,
)

# The package itself reads process environment only. The standalone demo also
# reads the repository's local .env so its documented command works directly.
DEMO_ENV_KEYS = {
    "USER_SERVICE_URL",
    "ORDER_SERVICE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "DIAGNOSTIC_POSTGRES_USER",
    "DIAGNOSTIC_POSTGRES_PASSWORD",
    "REDIS_HOST",
    "REDIS_PORT",
    "DOCKER_EXECUTOR_READ_URL",
    "DOCKER_EXECUTOR_READ_TOKEN",
    "DIAGNOSTICS_TOKEN",
    "DIAGNOSTIC_HTTP_TIMEOUT_SECONDS",
    "DIAGNOSTIC_POSTGRES_TIMEOUT_SECONDS",
    "DIAGNOSTIC_REDIS_TIMEOUT_SECONDS",
    "DIAGNOSTIC_DOCKER_TIMEOUT_SECONDS",
}


def load_demo_env() -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key not in DEMO_ENV_KEYS or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ[key] = value
    required = (
        "DOCKER_EXECUTOR_READ_TOKEN",
        "DIAGNOSTIC_POSTGRES_PASSWORD",
        "DIAGNOSTICS_TOKEN",
    )
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit(
            "Set diagnostic credentials in .env or the process environment: "
            + ", ".join(missing)
        )


async def main() -> None:
    results = await asyncio.gather(
        get_service_status("user-service"),
        get_service_logs("user-service", tail=10),
        check_http_endpoint("user-service", "/health"),
        check_postgres(),
        check_redis(),
        get_service_metrics("order-service"),
        check_service_dependency("order-service", "user-service"),
        get_dependency_config("order-service", "redis"),
    )
    for result in results:
        print(result.model_dump_json())


if __name__ == "__main__":
    load_demo_env()
    asyncio.run(main())
