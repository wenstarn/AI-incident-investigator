"""Allowlisted HTTP health checks."""

import time
from urllib.parse import urlsplit

import httpx

from diagnostic_tools.config import DiagnosticConfig
from diagnostic_tools.errors import connection_error
from diagnostic_tools.models import DiagnosticError, DiagnosticResult

PATHS = {
    "user-service": frozenset({"/health", "/health/db", "/users/0"}),
    "order-service": frozenset({"/health", "/health/db", "/health/redis"}),
}
MAX_RESPONSE_BYTES = 8192


async def check_http_endpoint(
    service: str, path: str, timeout_seconds: float | None = None
) -> DiagnosticResult:
    data = {"service": service, "path": path}
    if service not in PATHS or path not in PATHS[service]:
        return DiagnosticResult(
            tool="check_http_endpoint",
            status="error",
            data=data,
            error=DiagnosticError(
                type="invalid_target", message="Service or endpoint is not allowed"
            ),
        )
    if timeout_seconds is not None and (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not 0 < timeout_seconds <= 15
    ):
        return DiagnosticResult(
            tool="check_http_endpoint",
            status="error",
            data=data,
            error=DiagnosticError(type="invalid_parameter", message="Invalid timeout"),
        )
    config = DiagnosticConfig.from_env()
    base = (
        config.user_service_url
        if service == "user-service"
        else config.order_service_url
    )
    allowed_hosts = {
        "user-service": {"127.0.0.1", "localhost", "user-service"},
        "order-service": {"127.0.0.1", "localhost", "order-service"},
    }
    try:
        parsed = urlsplit(base)
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in allowed_hosts[service]
        or port != (8001 if service == "user-service" else 8002)
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return DiagnosticResult(
            tool="check_http_endpoint",
            status="error",
            data=data,
            error=DiagnosticError(
                type="configuration_error", message="Invalid service URL"
            ),
        )
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds or config.http_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream("GET", base.rstrip("/") + path) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        return DiagnosticResult(
                            tool="check_http_endpoint",
                            status="error",
                            data={**data, "status_code": response.status_code},
                            error=DiagnosticError(
                                type="response_too_large",
                                message="HTTP response exceeded limit",
                            ),
                        )
                code = response.status_code
    except httpx.HTTPError as error:
        return DiagnosticResult(
            tool="check_http_endpoint",
            status="degraded",
            data={
                **data,
                "reachable": False,
                "latency_ms": round((time.monotonic() - start) * 1000, 2),
            },
            error=connection_error(error),
        )
    return DiagnosticResult(
        tool="check_http_endpoint",
        status="ok" if 200 <= code < 300 else "degraded",
        data={
            **data,
            "reachable": True,
            "status_code": code,
            "latency_ms": round((time.monotonic() - start) * 1000, 2),
        },
    )
