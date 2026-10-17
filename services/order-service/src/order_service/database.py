import logging
from collections.abc import Iterator
from functools import lru_cache
from hashlib import sha256
from time import perf_counter

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session, sessionmaker

from order_service.config import (
    DEFAULT_DATABASE_SCHEMA,
    get_database_schema,
    get_database_url,
    get_slow_query_threshold_ms,
)

logger = logging.getLogger(__name__)


@lru_cache
def get_engine() -> Engine:
    engine = create_engine(
        get_database_url(),
        connect_args={"connect_timeout": 3},
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=5,
        pool_recycle=1800,
        execution_options={
            "schema_translate_map": {
                DEFAULT_DATABASE_SCHEMA: get_database_schema(),
            }
        },
    )
    _install_slow_query_logging(engine)
    return engine


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with get_session_factory()() as session:
        yield session


def check_database() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


def get_pool_stats() -> dict[str, int]:
    pool = get_engine().pool
    return {
        "size": pool.size(),
        "checked_out": pool.checkedout(),
        "checked_in": pool.checkedin(),
        "overflow": max(0, pool.overflow()),
    }


def log_database_error(error: SQLAlchemyError, operation: str) -> None:
    event_name = (
        "db_pool_timeout"
        if isinstance(error, SQLAlchemyTimeoutError)
        else "db_connection_failed"
    )
    logger.error(
        "Database operation failed",
        extra={
            "event": event_name,
            "operation": operation,
            "exception_type": type(error).__name__,
        },
    )


def _install_slow_query_logging(engine: Engine) -> None:
    threshold_ms = get_slow_query_threshold_ms()

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        connection.info.setdefault("query_started_at", []).append(perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        started = connection.info["query_started_at"].pop()
        duration_ms = (perf_counter() - started) * 1000
        if duration_ms >= threshold_ms:
            logger.warning(
                "Slow database query",
                extra={
                    "event": "db_query_slow",
                    "duration_ms": round(duration_ms, 3),
                    "query_id": sha256(statement.encode()).hexdigest()[:12],
                },
            )
