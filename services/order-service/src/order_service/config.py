import os

DEFAULT_DATABASE_SCHEMA = "order_service"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_PRODUCT_CACHE_TTL_SECONDS = 300
DEFAULT_CORS_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
DEFAULT_SLOW_QUERY_THRESHOLD_MS = 500.0


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return database_url


def get_database_schema() -> str:
    return os.getenv("ORDER_SERVICE_DB_SCHEMA", DEFAULT_DATABASE_SCHEMA)


def get_user_service_url() -> str:
    user_service_url = os.getenv("USER_SERVICE_URL")
    if not user_service_url:
        raise RuntimeError("USER_SERVICE_URL environment variable is required")
    return user_service_url.rstrip("/")


def get_user_service_timeout() -> float:
    try:
        timeout = float(os.getenv("USER_SERVICE_TIMEOUT_SECONDS", "2.0"))
    except ValueError as error:
        raise RuntimeError("USER_SERVICE_TIMEOUT_SECONDS must be a number") from error
    if timeout <= 0:
        raise RuntimeError("USER_SERVICE_TIMEOUT_SECONDS must be positive")
    return timeout


def get_redis_url() -> str:
    return os.getenv("REDIS_URL") or DEFAULT_REDIS_URL


def get_redis_timeout() -> float:
    try:
        timeout = float(os.getenv("REDIS_TIMEOUT_SECONDS", "1.0"))
    except ValueError as error:
        raise RuntimeError("REDIS_TIMEOUT_SECONDS must be a number") from error
    if timeout <= 0:
        raise RuntimeError("REDIS_TIMEOUT_SECONDS must be positive")
    return timeout


def get_product_cache_ttl() -> int:
    try:
        ttl = int(
            os.getenv(
                "PRODUCT_CACHE_TTL_SECONDS",
                str(DEFAULT_PRODUCT_CACHE_TTL_SECONDS),
            )
        )
    except ValueError as error:
        raise RuntimeError("PRODUCT_CACHE_TTL_SECONDS must be an integer") from error
    if ttl <= 0:
        raise RuntimeError("PRODUCT_CACHE_TTL_SECONDS must be positive")
    return ttl


def get_cors_allowed_origins() -> list[str]:
    configured = os.getenv("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ALLOWED_ORIGINS)
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def get_slow_query_threshold_ms() -> float:
    try:
        threshold = float(
            os.getenv("SLOW_QUERY_THRESHOLD_MS", str(DEFAULT_SLOW_QUERY_THRESHOLD_MS))
        )
    except ValueError as error:
        raise RuntimeError("SLOW_QUERY_THRESHOLD_MS must be a number") from error
    if threshold <= 0:
        raise RuntimeError("SLOW_QUERY_THRESHOLD_MS must be positive")
    return threshold
