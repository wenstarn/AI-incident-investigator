"""Read-only observations of service metrics and effective dependencies."""

import json
from urllib.parse import urlsplit

import httpx

from diagnostic_tools.config import DiagnosticConfig
from diagnostic_tools.errors import connection_error
from diagnostic_tools.models import DiagnosticError, DiagnosticResult

METRICS = {
    "requests_total",
    "errors_total",
    "cache_hits",
    "cache_misses",
    "dependency_timeouts_total",
    "dependency_errors_total",
    "redis_connection_errors_total",
}
DEPENDENCIES = frozenset({"user-service", "redis"})
MAX_RESPONSE_BYTES = 32768


def _order_url(config: DiagnosticConfig) -> str | None:
    try:
        parsed = urlsplit(config.order_service_url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "order-service"}
        or port != 8002
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return config.order_service_url.rstrip("/")


async def _get(
    url: str, path: str, timeout: float, token: str | None = None
) -> tuple[dict | None, DiagnosticError | None]:
    headers = {"X-Diagnostics-Token": token} if token else {}
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream("GET", url + path, headers=headers) as response:
                if response.status_code in {401, 403}:
                    return None, DiagnosticError(
                        type="forbidden", message="Diagnostics access denied"
                    )
                if response.status_code >= 400:
                    return None, DiagnosticError(
                        type="http_error", message="Service diagnostics unavailable"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        return None, DiagnosticError(
                            type="response_too_large",
                            message="Diagnostics response exceeded limit",
                        )
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Invalid diagnostics response")
                return payload, None
    except (httpx.HTTPError, ValueError) as error:
        return None, connection_error(error)


async def get_service_metrics(service: str) -> DiagnosticResult:
    data = {"service": service}
    if service not in {"user-service", "order-service"}:
        return DiagnosticResult(
            tool="get_service_metrics",
            status="error",
            data=data,
            error=DiagnosticError(
                type="invalid_service", message="Service is not allowed"
            ),
        )
    config = DiagnosticConfig.from_env()
    if service == "order-service":
        url = _order_url(config)
    else:
        # Reuse the health tool's strict service URL validation by checking a
        # fixed origin here; no request-supplied URL is accepted.
        try:
            parsed = urlsplit(config.user_service_url)
            port = parsed.port
        except ValueError:
            return DiagnosticResult(
                tool="get_service_metrics",
                status="error",
                data=data,
                error=DiagnosticError(
                    type="configuration_error", message="Invalid service URL"
                ),
            )
        url = (
            config.user_service_url.rstrip("/")
            if (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost", "user-service"}
                and port == 8001
                and not parsed.username
                and not parsed.password
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
            else None
        )
    if url is None:
        return DiagnosticResult(
            tool="get_service_metrics",
            status="error",
            data=data,
            error=DiagnosticError(
                type="configuration_error", message="Invalid service URL"
            ),
        )
    payload, error = await _get(url, "/internal/stats", config.http_timeout_seconds)
    if error:
        return DiagnosticResult(
            tool="get_service_metrics", status="error", data=data, error=error
        )
    assert payload is not None
    values = {
        key: value
        for key, value in payload.items()
        if key in METRICS and isinstance(value, int) and not isinstance(value, bool)
    }
    pool = payload.get("database_pool")
    if isinstance(pool, dict):
        values["database_pool"] = {
            key: value
            for key, value in pool.items()
            if key in {"size", "checked_out", "checked_in", "overflow"}
            and isinstance(value, int)
            and not isinstance(value, bool)
        }
    return DiagnosticResult(
        tool="get_service_metrics", status="ok", data={**data, "metrics": values}
    )


async def check_service_dependency(
    source_service: str, dependency: str
) -> DiagnosticResult:
    data = {"source_service": source_service, "dependency": dependency}
    if source_service != "order-service" or dependency not in DEPENDENCIES:
        return DiagnosticResult(
            tool="check_service_dependency",
            status="error",
            data=data,
            error=DiagnosticError(
                type="invalid_target", message="Dependency is not allowed"
            ),
        )
    config = DiagnosticConfig.from_env()
    url = _order_url(config)
    if url is None or not config.diagnostics_token:
        return DiagnosticResult(
            tool="check_service_dependency",
            status="error",
            data=data,
            error=DiagnosticError(
                type="configuration_error",
                message="Diagnostics connection is not configured",
            ),
        )
    payload, error = await _get(
        url,
        f"/internal/diagnostics/dependencies/{dependency}",
        config.http_timeout_seconds,
        config.diagnostics_token,
    )
    if error:
        return DiagnosticResult(
            tool="check_service_dependency", status="error", data=data, error=error
        )
    assert payload is not None
    if not isinstance(payload.get("reachable"), bool) or not isinstance(
        payload.get("latency_ms"), (int, float)
    ):
        return DiagnosticResult(
            tool="check_service_dependency",
            status="error",
            data=data,
            error=DiagnosticError(
                type="invalid_response", message="Invalid dependency response"
            ),
        )
    result_data = {
        **data,
        "reachable": payload["reachable"],
        "latency_ms": payload["latency_ms"],
    }
    if isinstance(payload.get("status_code"), int):
        result_data["status_code"] = payload["status_code"]
    status_code = result_data.get("status_code")
    if payload["reachable"] and not (status_code and status_code >= 500):
        return DiagnosticResult(
            tool="check_service_dependency", status="ok", data=result_data
        )
    kind = payload.get("error_type")
    if kind not in {
        "timeout",
        "connection_refused",
        "request_error",
        "unexpected_response",
        "http_error",
    }:
        kind = "dependency_error"
    return DiagnosticResult(
        tool="check_service_dependency",
        status="degraded",
        data=result_data,
        error=DiagnosticError(type=kind, message="Dependency probe failed"),
    )


async def get_dependency_config(service: str, dependency: str) -> DiagnosticResult:
    data = {"service": service, "dependency": dependency}
    if service != "order-service" or dependency not in DEPENDENCIES:
        return DiagnosticResult(
            tool="get_dependency_config",
            status="error",
            data=data,
            error=DiagnosticError(
                type="invalid_target", message="Dependency is not allowed"
            ),
        )
    config = DiagnosticConfig.from_env()
    url = _order_url(config)
    if url is None or not config.diagnostics_token:
        return DiagnosticResult(
            tool="get_dependency_config",
            status="error",
            data=data,
            error=DiagnosticError(
                type="configuration_error",
                message="Diagnostics connection is not configured",
            ),
        )
    payload, error = await _get(
        url,
        f"/internal/diagnostics/config/{dependency}",
        config.http_timeout_seconds,
        config.diagnostics_token,
    )
    if error:
        return DiagnosticResult(
            tool="get_dependency_config", status="error", data=data, error=error
        )
    assert payload is not None
    if not isinstance(payload.get("host"), str) or not isinstance(
        payload.get("port"), int
    ):
        return DiagnosticResult(
            tool="get_dependency_config",
            status="error",
            data=data,
            error=DiagnosticError(
                type="invalid_response",
                message="Invalid dependency configuration response",
            ),
        )
    data.update(host=payload["host"], port=payload["port"])
    if isinstance(payload.get("timeout_seconds"), (int, float)):
        data["timeout_seconds"] = payload["timeout_seconds"]
    return DiagnosticResult(tool="get_dependency_config", status="ok", data=data)
