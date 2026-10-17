"""Read-only Docker Executor client. No Docker socket is mounted here."""

import httpx

from diagnostic_tools.config import DiagnosticConfig
from diagnostic_tools.errors import connection_error
from diagnostic_tools.models import DiagnosticError, DiagnosticResult

SERVICES = frozenset({"user-service", "order-service", "postgres", "redis", "frontend"})
MAX_LOG_BYTES = 65536


async def _read(
    path: str, params: dict | None = None
) -> tuple[dict | None, DiagnosticError | None]:
    config = DiagnosticConfig.from_env()
    if not config.docker_executor_read_token:
        return None, DiagnosticError(
            type="configuration_error",
            message="Read-only executor token is not configured",
        )
    try:
        async with httpx.AsyncClient(
            timeout=config.docker_executor_timeout_seconds, trust_env=False
        ) as client:
            response = await client.get(
                config.docker_executor_read_url.rstrip("/") + path,
                params=params,
                headers={"X-Executor-Read-Token": config.docker_executor_read_token},
            )
        if response.status_code == 404:
            return None, DiagnosticError(
                type="container_not_found", message="Container not found"
            )
        if response.status_code in {401, 403}:
            return None, DiagnosticError(
                type="forbidden", message="Read-only executor access denied"
            )
        response.raise_for_status()
        if len(response.content) > MAX_LOG_BYTES * 2:
            return None, DiagnosticError(
                type="response_too_large", message="Executor response exceeded limit"
            )
        return response.json(), None
    except (httpx.HTTPError, ValueError) as error:
        return None, connection_error(error)


async def get_service_status(service: str) -> DiagnosticResult:
    if service not in SERVICES:
        return DiagnosticResult(
            tool="get_service_status",
            status="error",
            data={"service": service},
            error=DiagnosticError(
                type="invalid_service", message="Service is not allowed"
            ),
        )
    payload, error = await _read(f"/read-only/services/{service}/status")
    if error:
        return DiagnosticResult(
            tool="get_service_status",
            status="error",
            data={"service": service},
            error=error,
        )
    assert payload is not None
    data = {
        "service": service,
        "container_status": payload["container_status"],
        "health_status": payload.get("health_status"),
        "started_at": payload.get("started_at"),
    }
    healthy = data["container_status"] == "running" and data["health_status"] not in {
        "unhealthy",
        "starting",
    }
    return DiagnosticResult(
        tool="get_service_status", status="ok" if healthy else "degraded", data=data
    )


async def get_service_logs(
    service: str, tail: int = 50, since_seconds: int | None = None
) -> DiagnosticResult:
    if service not in SERVICES:
        return DiagnosticResult(
            tool="get_service_logs",
            status="error",
            data={"service": service},
            error=DiagnosticError(
                type="invalid_service", message="Service is not allowed"
            ),
        )
    if (
        isinstance(tail, bool)
        or not isinstance(tail, int)
        or not 1 <= tail <= 200
        or (
            since_seconds is not None
            and (
                isinstance(since_seconds, bool)
                or not isinstance(since_seconds, int)
                or since_seconds < 1
                or since_seconds > 86400
            )
        )
    ):
        return DiagnosticResult(
            tool="get_service_logs",
            status="error",
            data={"service": service},
            error=DiagnosticError(
                type="invalid_parameter",
                message="tail must be 1–200 and since_seconds 1–86400",
            ),
        )
    params = {"tail": tail}
    if since_seconds is not None:
        params["since_seconds"] = since_seconds
    payload, error = await _read(f"/read-only/services/{service}/logs", params)
    if error:
        return DiagnosticResult(
            tool="get_service_logs",
            status="error",
            data={"service": service},
            error=error,
        )
    assert payload is not None
    return DiagnosticResult(
        tool="get_service_logs", status="ok", data={"service": service, **payload}
    )
