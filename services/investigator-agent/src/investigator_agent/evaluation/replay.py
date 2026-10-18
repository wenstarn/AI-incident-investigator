"""Replay worlds: deterministic stand-ins for the live stack.

A world is a small state model: stopped containers, effective ports in
order-service and injected User Service latency. Every diagnostic answer is
derived from that state, so answers stay consistent whatever the agent calls.

Application logs and counters come from simulating the same symptom-reproducing
business requests that the live harness sends (cases.SYMPTOM_REQUESTS), so the
agent sees the same kind of evidence in replay and live runs.

Statuses, error types and latencies were measured on the live stack (Docker
Compose, 2026-10-18) and agree with docs/incident-guide.md. Notable behaviour:
names of stopped containers stop resolving inside the Compose network, so calls
from the application services to them hang for seconds before failing, while
direct checks from the investigator fail fast. When PostgreSQL is down, User
Service lookups become slow enough for the Order Service probe to time out.
Probes near a timeout boundary vary between live runs; values follow the
fidelity mode of the evaluation CLI, which probes in the same order as a live
evaluation (activate, reproduce the symptom, then diagnose).

This module is used only by the evaluation harness and unit tests; agents see
nothing but tool results.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from diagnostic_tools.http_tools import PATHS
from diagnostic_tools.models import DiagnosticError, DiagnosticResult

from investigator_agent.evaluation.cases import SYMPTOM_REQUESTS, Request

STARTED_AT = "2026-10-18T08:29:50.985541802Z"
LOG_TS = "2026-10-18T08:31:{second:02d}.{micro:06d}000Z"
USER_SERVICE_PORT = 8001
REDIS_PORT = 6379
BAD_PORT = 9999
USER_TIMEOUT_S = 2.0
REDIS_TIMEOUT_S = 1.0
DEFAULT_HTTP_TIMEOUT_S = 5.0
# Measured latencies, milliseconds.
STOPPED_TARGET_PROBE_MS = {"user-service": 3495.35, "redis": 5000.0}
DB_DOWN_MS = {
    "user /health/db": 4051.25,
    "order /health/db": 6780.84,
    "user lookup": 8016.0,
    "user get_user": 8016.0,
    "order get_product": 8037.0,
    "order list_products": 3761.0,
}
REDIS_DOWN_HEALTH_MS = 4051.35
REDIS_DOWN_PRODUCT_MS = 11787.0


@dataclass(frozen=True)
class WorldState:
    stopped: frozenset[str] = frozenset()
    order_user_port: int = USER_SERVICE_PORT
    order_redis_port: int = REDIS_PORT
    user_delay_ms: int = 0
    traffic: tuple[Request, ...] = ()


def _state(name: str, **kwargs: Any) -> WorldState:
    return WorldState(traffic=tuple(SYMPTOM_REQUESTS.get(name, [])), **kwargs)


WORLD_STATES: dict[str, WorldState] = {
    "healthy": _state("healthy"),
    "INC-001": _state("INC-001", stopped=frozenset({"user-service"})),
    "INC-002": _state("INC-002", stopped=frozenset({"redis"})),
    "INC-003": _state("INC-003", stopped=frozenset({"postgres"})),
    "INC-004": _state("INC-004", order_user_port=BAD_PORT),
    "INC-005": _state("INC-005", user_delay_ms=5000),
    "INC-006": _state("INC-006", order_redis_port=BAD_PORT),
}


def _result(
    tool: str,
    status: str,
    data: dict[str, Any],
    error: tuple[str, str] | None = None,
) -> DiagnosticResult:
    return DiagnosticResult(
        tool=tool,
        status=status,
        data=data,
        error=DiagnosticError(type=error[0], message=error[1]) if error else None,
    )


@dataclass
class _LogBuilder:
    service: str
    lines: list[str] = field(default_factory=list)

    def _ts(self) -> str:
        index = len(self.lines)
        return LOG_TS.format(second=index % 60, micro=(index * 7919) % 1_000_000)

    def raw(self, text: str) -> None:
        self.lines.append(f"{self._ts()} {text}")

    def event(self, level: str, event: str, message: str, **fields: Any) -> None:
        payload = {
            "timestamp": self._ts()[:23] + "Z",
            "service": self.service,
            "level": level,
            "event": event,
            "request_id": f"req{len(self.lines):04d}",
            "message": message,
            **fields,
        }
        self.raw(json.dumps(payload, separators=(",", ":")))

    def http(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client: str = "172.21.0.1:43786",
    ) -> None:
        self.event(
            "INFO", "http_request", "HTTP request started", method=method, path=path
        )
        self.event(
            "INFO",
            "http_response",
            "HTTP request completed",
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_ms,
        )
        self.raw(f'INFO:     {client} - "{method} {path} HTTP/1.1" {status_code}')


@dataclass
class _Traffic:
    """Effects of the symptom-reproducing requests on logs and counters."""

    order: _LogBuilder = field(default_factory=lambda: _LogBuilder("order-service"))
    user: _LogBuilder = field(default_factory=lambda: _LogBuilder("user-service"))
    order_counters: dict[str, int] = field(
        default_factory=lambda: {
            "requests_total": 41,
            "errors_total": 0,
            "cache_hits": 3,
            "cache_misses": 2,
            "dependency_timeouts_total": 0,
            "dependency_errors_total": 0,
            "redis_connection_errors_total": 0,
        }
    )
    user_counters: dict[str, int] = field(
        default_factory=lambda: {
            "requests_total": 30,
            "errors_total": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }
    )


class ReplayWorld:
    def __init__(self, name: str, state: WorldState) -> None:
        self.name = name
        self.state = state

    @classmethod
    def named(cls, name: str) -> "ReplayWorld":
        return cls(name, WORLD_STATES[name])

    def backend(self) -> dict[str, Callable[..., Awaitable[DiagnosticResult]]]:
        def wrap(method: Callable[..., DiagnosticResult]):
            async def call(**kwargs: Any) -> DiagnosticResult:
                return method(**kwargs)

            return call

        return {
            name: wrap(getattr(self, name))
            for name in (
                "get_service_status",
                "get_service_logs",
                "check_http_endpoint",
                "check_postgres",
                "check_redis",
                "get_service_metrics",
                "check_service_dependency",
                "get_dependency_config",
            )
        }

    # --- derived facts ---------------------------------------------------
    def _down(self, service: str) -> bool:
        return service in self.state.stopped

    def _user_lookup(self) -> tuple[int, float]:
        """Status and latency of GET /users/{id} answered by User Service."""
        if self.state.user_delay_ms:
            return 404, self.state.user_delay_ms + 8.4
        if self._down("postgres"):
            return 503, DB_DOWN_MS["user lookup"]
        return 404, 10.04

    def _user_call_from_order(self) -> tuple[str, float]:
        """Outcome of order-service calling User Service.

        One of ok, connection_refused, timeout or http_error (User Service
        answered 503 because its database is down).
        """
        if self._down("user-service"):
            return "connection_refused", STOPPED_TARGET_PROBE_MS["user-service"]
        if self.state.order_user_port != USER_SERVICE_PORT:
            return "connection_refused", 5.42
        if self._down("postgres"):
            return "http_error", 1520.4
        _, latency = self._user_lookup()
        if latency > USER_TIMEOUT_S * 1000:
            return "timeout", USER_TIMEOUT_S * 1000 + 10.5
        return "ok", 6.09

    def _redis_from_order(self) -> tuple[str, float, str]:
        """Outcome, latency and log target of order-service calling Redis."""
        if self._down("redis"):
            # The in-service probe outlives the diagnostic tool's own timeout.
            return "probe_timeout", STOPPED_TARGET_PROBE_MS["redis"], "redis"
        if self.state.order_redis_port != REDIS_PORT:
            return "connection_refused", 1.48, f"redis:{self.state.order_redis_port}"
        return "ok", 3.12, f"redis:{REDIS_PORT}"

    # --- simulated business traffic ---------------------------------------
    def _traffic(self) -> _Traffic:
        traffic = _Traffic()
        for log in (traffic.order, traffic.user):
            log.event("INFO", "service_started", "Service started")
            for _ in range(3):
                log.http("GET", "/health", 200, 1.2, client="127.0.0.1:45952")
        for base, method, path, _ in self.state.traffic:
            if base == "user":
                self._simulate_user_lookup(traffic, path)
            elif method == "POST":
                self._simulate_order(traffic)
            elif path == "/products":
                self._simulate_catalog(traffic)
            else:
                self._simulate_product(traffic, path)
        return traffic

    def _count(self, counters: dict[str, int], status_code: int) -> None:
        counters["requests_total"] += 1
        if status_code >= 500:
            counters["errors_total"] += 1

    def _simulate_user_lookup(self, traffic: _Traffic, path: str) -> None:
        if self._down("user-service"):
            return
        if self.state.user_delay_ms:
            traffic.user.event(
                "WARNING",
                "fault_injection_applied",
                "User lookup delayed by runtime fault",
                operation="get_user",
            )
        code, latency = self._user_lookup()
        if self._down("postgres"):
            latency = DB_DOWN_MS["user get_user"]
            traffic.user.event(
                "ERROR",
                "db_connection_failed",
                "Database operation failed",
                operation="get_user",
                exception_type="OperationalError",
            )
        traffic.user.http("GET", path, code, latency)
        self._count(traffic.user_counters, code)

    def _simulate_order(self, traffic: _Traffic) -> None:
        outcome, latency = self._user_call_from_order()
        target = f"user-service:{self.state.order_user_port}"
        if self.state.order_user_port == USER_SERVICE_PORT:
            self._simulate_user_lookup(traffic, "/users/2000000000")
        counters = traffic.order_counters
        if outcome == "ok":
            code = 404
        elif outcome == "timeout":
            code = 504
            counters["dependency_timeouts_total"] += 1
            traffic.order.event(
                "ERROR",
                "dependency_connection_failed",
                "User Service request timed out",
                target=target,
                exception_type="ReadTimeout",
            )
        else:
            code = 502
            counters["dependency_errors_total"] += 1
            traffic.order.event(
                "ERROR",
                "dependency_connection_failed",
                "User Service request failed",
                target=target,
                exception_type="ConnectError",
            )
            if self._down("user-service"):
                latency = 8028.0  # two failed name resolutions
        traffic.order.http("POST", "/orders", code, round(latency + 15.7, 3))
        self._count(counters, code)

    def _simulate_product(self, traffic: _Traffic, path: str) -> None:
        counters = traffic.order_counters
        if self._down("postgres"):
            traffic.order.event(
                "ERROR",
                "db_connection_failed",
                "Database operation failed",
                operation="get_product",
                exception_type="OperationalError",
            )
            traffic.order.http("GET", path, 503, DB_DOWN_MS["order get_product"])
            self._count(counters, 503)
            return
        outcome, _, target = self._redis_from_order()
        if outcome == "ok":
            counters["cache_hits"] += 1
            latency = 14.2
        else:
            counters["cache_misses"] += 1
            for operation in ("read", "write"):
                counters["redis_connection_errors_total"] += 1
                traffic.order.event(
                    "ERROR",
                    "redis_connection_failed",
                    "Redis cache operation failed",
                    operation=operation,
                    target=target,
                    exception_type="ConnectionError",
                )
            latency = REDIS_DOWN_PRODUCT_MS if self._down("redis") else 49.0
        traffic.order.http("GET", path, 200, latency)
        self._count(counters, 200)

    def _simulate_catalog(self, traffic: _Traffic) -> None:
        if self._down("postgres"):
            traffic.order.event(
                "ERROR",
                "db_connection_failed",
                "Database operation failed",
                operation="list_products",
                exception_type="OperationalError",
            )
            traffic.order.http(
                "GET", "/products", 503, DB_DOWN_MS["order list_products"]
            )
            self._count(traffic.order_counters, 503)
        else:
            traffic.order.http("GET", "/products", 200, 21.0)
            self._count(traffic.order_counters, 200)

    # --- tools -------------------------------------------------------------
    def get_service_status(self, service: str) -> DiagnosticResult:
        down = self._down(service)
        return _result(
            "get_service_status",
            "degraded" if down else "ok",
            {
                "service": service,
                "container_status": "exited" if down else "running",
                "health_status": "unhealthy" if down else "healthy",
                "started_at": STARTED_AT,
            },
        )

    def get_service_logs(
        self, service: str, tail: int = 50, since_seconds: int | None = None
    ) -> DiagnosticResult:
        lines = self._logs(service)
        selected = lines[-tail:]
        return _result(
            "get_service_logs",
            "ok",
            {
                "service": service,
                "logs": "".join(line + "\n" for line in selected),
                "truncated": len(selected) < len(lines),
                "container_status": "exited" if self._down(service) else "running",
            },
        )

    def _logs(self, service: str) -> list[str]:
        if service in {"order-service", "user-service"}:
            traffic = self._traffic()
            log = traffic.order if service == "order-service" else traffic.user
            if self._down(service):
                for line in (
                    "INFO:     Shutting down",
                    "INFO:     Waiting for application shutdown.",
                    "INFO:     Application shutdown complete.",
                    "INFO:     Finished server process [1]",
                ):
                    log.raw(line)
            return log.lines
        log = _LogBuilder(service)
        if service == "postgres":
            log.raw(
                "2026-10-18 08:29:40.101 UTC [1] LOG:  "
                "database system is ready to accept connections"
            )
            if self._down(service):
                log.raw(
                    "2026-10-18 08:31:05.132 UTC [27] LOG:  "
                    "checkpoint complete: wrote 6 buffers (0.0%)"
                )
                log.raw(
                    "2026-10-18 08:31:05.135 UTC [1] LOG:  database system is shut down"
                )
        elif service == "redis":
            log.raw("1:M 18 Oct 2026 08:29:39.201 * Ready to accept connections tcp")
            if self._down(service):
                log.raw("1:M 18 Oct 2026 08:30:50.128 * DB saved on disk")
                log.raw(
                    "1:M 18 Oct 2026 08:30:50.129 # "
                    "Redis is now ready to exit, bye bye..."
                )
        else:
            for _ in range(3):
                log.raw(
                    "172.18.0.1 - - [18/Oct/2026:08:31:00 +0000] "
                    '"GET /api/products HTTP/1.1" 200 412'
                )
        return log.lines

    def check_http_endpoint(
        self, service: str, path: str, timeout_seconds: float | None = None
    ) -> DiagnosticResult:
        data: dict[str, Any] = {"service": service, "path": path}
        if service not in PATHS or path not in PATHS[service]:
            return _result(
                "check_http_endpoint",
                "error",
                data,
                ("invalid_target", "Service or endpoint is not allowed"),
            )
        if self._down(service):
            return _result(
                "check_http_endpoint",
                "degraded",
                {**data, "reachable": False, "latency_ms": 9.35},
                ("connection_refused", "Connection failed"),
            )
        code, latency = 200, 9.13
        if path == "/health/db" and self._down("postgres"):
            key = "user /health/db" if service == "user-service" else "order /health/db"
            code, latency = 503, DB_DOWN_MS[key]
        elif path == "/health/redis" and self._down("redis"):
            # The health check uses the static Redis address, so a wrong
            # runtime address in order-service does not affect it.
            code, latency = 503, REDIS_DOWN_HEALTH_MS
        elif path == "/users/0":
            code, latency = self._user_lookup()
        timeout_ms = (timeout_seconds or DEFAULT_HTTP_TIMEOUT_S) * 1000
        if latency > timeout_ms:
            return _result(
                "check_http_endpoint",
                "degraded",
                {**data, "reachable": False, "latency_ms": round(timeout_ms + 37.7, 2)},
                ("timeout", "Connection timed out"),
            )
        return _result(
            "check_http_endpoint",
            "ok" if 200 <= code < 300 else "degraded",
            {**data, "reachable": True, "status_code": code, "latency_ms": latency},
        )

    def check_postgres(self) -> DiagnosticResult:
        if self._down("postgres"):
            return _result(
                "check_postgres",
                "degraded",
                {"reachable": False, "query_succeeded": False, "latency_ms": 1.46},
                ("connection_refused", "Connection refused"),
            )
        return _result(
            "check_postgres",
            "ok",
            {"reachable": True, "query_succeeded": True, "latency_ms": 9.04},
        )

    def check_redis(self) -> DiagnosticResult:
        if self._down("redis"):
            return _result(
                "check_redis",
                "degraded",
                {"reachable": False, "ping": False, "latency_ms": 1.09},
                ("connection_refused", "Connection failed"),
            )
        return _result(
            "check_redis", "ok", {"reachable": True, "ping": True, "latency_ms": 2.08}
        )

    def get_service_metrics(self, service: str) -> DiagnosticResult:
        data: dict[str, Any] = {"service": service}
        if service not in {"user-service", "order-service"}:
            return _result(
                "get_service_metrics",
                "error",
                data,
                ("invalid_service", "Service is not allowed"),
            )
        if self._down(service):
            return _result(
                "get_service_metrics",
                "error",
                data,
                ("connection_refused", "Connection failed"),
            )
        traffic = self._traffic()
        counters = (
            traffic.order_counters
            if service == "order-service"
            else traffic.user_counters
        )
        pool = {"size": 5, "checked_out": 0, "checked_in": 1, "overflow": 0}
        if self._down("postgres"):
            pool = {"size": 5, "checked_out": 1, "checked_in": 0, "overflow": 0}
        return _result(
            "get_service_metrics",
            "ok",
            {**data, "metrics": {**counters, "database_pool": pool}},
        )

    def check_service_dependency(
        self, source_service: str, dependency: str
    ) -> DiagnosticResult:
        data: dict[str, Any] = {
            "source_service": source_service,
            "dependency": dependency,
        }
        if source_service != "order-service" or dependency not in {
            "user-service",
            "redis",
        }:
            return _result(
                "check_service_dependency",
                "error",
                data,
                ("invalid_target", "Dependency is not allowed"),
            )
        if dependency == "redis":
            outcome, latency, _ = self._redis_from_order()
            extra: dict[str, Any] = {}
        else:
            outcome, latency = self._user_call_from_order()
            extra = {"status_code": 404}
        if outcome == "http_error":
            return _result(
                "check_service_dependency",
                "degraded",
                {**data, "reachable": True, "latency_ms": latency, "status_code": 503},
                ("http_error", "Dependency probe failed"),
            )
        if outcome == "ok":
            return _result(
                "check_service_dependency",
                "ok",
                {**data, "reachable": True, "latency_ms": latency, **extra},
            )
        if outcome == "probe_timeout":
            return _result(
                "check_service_dependency",
                "error",
                data,
                ("timeout", "Connection timed out"),
            )
        return _result(
            "check_service_dependency",
            "degraded",
            {**data, "reachable": False, "latency_ms": latency},
            (outcome, "Dependency probe failed"),
        )

    def get_dependency_config(self, service: str, dependency: str) -> DiagnosticResult:
        data: dict[str, Any] = {"service": service, "dependency": dependency}
        if service != "order-service" or dependency not in {"user-service", "redis"}:
            return _result(
                "get_dependency_config",
                "error",
                data,
                ("invalid_target", "Dependency is not allowed"),
            )
        if dependency == "user-service":
            data.update(
                host="user-service",
                port=self.state.order_user_port,
                timeout_seconds=USER_TIMEOUT_S,
            )
        else:
            data.update(
                host="redis",
                port=self.state.order_redis_port,
                timeout_seconds=REDIS_TIMEOUT_S,
            )
        return _result("get_dependency_config", "ok", data)
