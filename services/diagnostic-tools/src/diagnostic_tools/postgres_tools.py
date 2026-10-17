"""Direct read-only PostgreSQL probe."""

import time

import psycopg

from diagnostic_tools.config import DiagnosticConfig
from diagnostic_tools.errors import connection_error
from diagnostic_tools.models import DiagnosticError, DiagnosticResult


async def check_postgres() -> DiagnosticResult:
    config = DiagnosticConfig.from_env()
    if not config.postgres_password:
        return DiagnosticResult(
            tool="check_postgres",
            status="error",
            data={"reachable": False},
            error=DiagnosticError(
                type="configuration_error",
                message="Diagnostic database credential is not configured",
            ),
        )
    start = time.monotonic()
    try:
        async with await psycopg.AsyncConnection.connect(
            host=config.postgres_host,
            port=config.postgres_port,
            dbname=config.postgres_db,
            user=config.postgres_user,
            password=config.postgres_password,
            connect_timeout=config.postgres_timeout_seconds,
            options=(
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={int(config.postgres_timeout_seconds * 1000)}"
            ),
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                value = (await cursor.fetchone())[0]
    except (psycopg.Error, OSError, TimeoutError) as error:
        return DiagnosticResult(
            tool="check_postgres",
            status="degraded",
            data={
                "reachable": False,
                "query_succeeded": False,
                "latency_ms": round((time.monotonic() - start) * 1000, 2),
            },
            error=connection_error(error),
        )
    return DiagnosticResult(
        tool="check_postgres",
        status="ok" if value == 1 else "degraded",
        data={
            "reachable": True,
            "query_succeeded": value == 1,
            "latency_ms": round((time.monotonic() - start) * 1000, 2),
        },
    )
