"""Safe error categories. Never include library exception text or credentials."""

import errno

import httpx
import psycopg
import redis.exceptions

from diagnostic_tools.models import DiagnosticError


def connection_error(error: BaseException) -> DiagnosticError:
    if isinstance(
        error, (TimeoutError, httpx.TimeoutException, redis.exceptions.TimeoutError)
    ):
        return DiagnosticError(type="timeout", message="Connection timed out")
    if isinstance(error, psycopg.OperationalError):
        detail = str(error).lower()
        if error.sqlstate == "28P01" or "password authentication failed" in detail:
            return DiagnosticError(
                type="authentication_failed", message="Authentication failed"
            )
        if "connection refused" in detail:
            return DiagnosticError(
                type="connection_refused", message="Connection refused"
            )
        if "timeout" in detail or "timed out" in detail:
            return DiagnosticError(type="timeout", message="Connection timed out")
    if isinstance(
        error,
        (ConnectionRefusedError, httpx.ConnectError, redis.exceptions.ConnectionError),
    ):
        return DiagnosticError(type="connection_refused", message="Connection failed")
    if isinstance(error, OSError) and error.errno == errno.ECONNREFUSED:
        return DiagnosticError(type="connection_refused", message="Connection refused")
    return DiagnosticError(type="dependency_error", message="Dependency check failed")
