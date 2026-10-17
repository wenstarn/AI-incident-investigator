import os

DEFAULT_DATABASE_SCHEMA = "user_service"
DEFAULT_CORS_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
DEFAULT_SLOW_QUERY_THRESHOLD_MS = 500.0


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return database_url


def get_database_schema() -> str:
    return os.getenv("USER_SERVICE_DB_SCHEMA", DEFAULT_DATABASE_SCHEMA)


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
