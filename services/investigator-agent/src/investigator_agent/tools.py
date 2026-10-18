"""Tool registry exposing read-only diagnostics to the language model.

Only the eight ``diagnostic_tools`` functions can be offered. Incident control,
shell commands, arbitrary SQL and arbitrary URLs are deliberately absent.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from diagnostic_tools.models import DiagnosticResult
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ContainerService = Literal[
    "user-service", "order-service", "postgres", "redis", "frontend"
]
AppService = Literal["user-service", "order-service"]
HttpPath = Literal["/health", "/health/db", "/health/redis", "/users/0"]
Dependency = Literal["user-service", "redis"]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class ServiceStatusArgs(_Args):
    service: ContainerService


class ServiceLogsArgs(_Args):
    service: ContainerService
    tail: int = Field(default=50, ge=1, le=200, description="Number of last lines")
    since_seconds: int | None = Field(
        default=None, ge=1, le=86400, description="Only lines newer than this"
    )


class HttpEndpointArgs(_Args):
    service: AppService
    path: HttpPath = Field(
        description=(
            "user-service allows /health, /health/db, /users/0; "
            "order-service allows /health, /health/db, /health/redis"
        )
    )
    timeout_seconds: float | None = Field(
        default=None, gt=0, le=15, description="Client timeout, default 5 s"
    )


class ServiceMetricsArgs(_Args):
    service: AppService


class ServiceDependencyArgs(_Args):
    source_service: Literal["order-service"]
    dependency: Dependency


class DependencyConfigArgs(_Args):
    service: Literal["order-service"]
    dependency: Dependency


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[BaseModel]


TOOL_SPECS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "get_service_status",
            "Docker container state (running/exited) and Docker health of a service.",
            ServiceStatusArgs,
        ),
        ToolSpec(
            "get_service_logs",
            "Last log lines of a service container with timestamps. Application "
            "services write JSON events (http_response, dependency_connection_failed, "
            "db_connection_failed, redis_connection_failed, ...).",
            ServiceLogsArgs,
        ),
        ToolSpec(
            "check_http_endpoint",
            "HTTP GET to an allowlisted endpoint, made directly from the "
            "investigator. Returns reachability, status code and latency.",
            HttpEndpointArgs,
        ),
        ToolSpec(
            "check_postgres",
            "Direct connection to PostgreSQL from the investigator and SELECT 1.",
            NoArgs,
        ),
        ToolSpec(
            "check_redis",
            "Direct Redis PING from the investigator using the standard address.",
            NoArgs,
        ),
        ToolSpec(
            "get_service_metrics",
            "In-memory counters of a service (/internal/stats): requests, errors, "
            "cache, dependency errors and DB pool snapshot.",
            ServiceMetricsArgs,
        ),
        ToolSpec(
            "check_service_dependency",
            "Probe a dependency FROM INSIDE order-service, using the address and "
            "timeout that order-service actually uses.",
            ServiceDependencyArgs,
        ),
        ToolSpec(
            "get_dependency_config",
            "Effective host, port and timeout that order-service currently uses "
            "for a dependency (no secrets).",
            DependencyConfigArgs,
        ),
    )
}

Backend = Mapping[str, Callable[..., Awaitable[DiagnosticResult]]]


def live_backend() -> dict[str, Callable[..., Awaitable[DiagnosticResult]]]:
    """Real read-only diagnostics against the running stack."""
    import diagnostic_tools

    return {name: getattr(diagnostic_tools, name) for name in TOOL_SPECS}


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop pydantic titles: they add tokens and confuse some small models."""
    if isinstance(schema, dict):
        return {
            key: _clean_schema(value) for key, value in schema.items() if key != "title"
        }
    if isinstance(schema, list):
        return [_clean_schema(item) for item in schema]
    return schema


def function_schema(
    name: str, description: str, arguments: type[BaseModel]
) -> dict[str, Any]:
    parameters = _clean_schema(arguments.model_json_schema())
    parameters.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def validation_summary(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}: {item['msg']}"
        for item in error.errors()
    )


def parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Arguments must be a JSON object")
    return value


class ToolCallRecord(BaseModel):
    agent: str
    tool: str
    arguments: dict[str, Any]
    status: str
    result: dict[str, Any]
    duration_ms: float
    duplicate: bool = False


class ToolRegistry:
    def __init__(
        self,
        backend: Backend,
        names: Iterable[str] | None = None,
        max_output_chars: int = 6000,
    ) -> None:
        selected = tuple(TOOL_SPECS if names is None else names)
        unknown = [name for name in selected if name not in TOOL_SPECS]
        if unknown:
            raise ValueError(f"Unknown tools: {', '.join(unknown)}")
        missing = [name for name in selected if name not in backend]
        if missing:
            raise ValueError(f"Backend does not provide: {', '.join(missing)}")
        self._backend = backend
        self.names = selected
        self.max_output_chars = max_output_chars

    def subset(self, names: Iterable[str]) -> "ToolRegistry":
        names = tuple(names)
        outside = [name for name in names if name not in self.names]
        if outside:
            raise ValueError(f"Tools not available: {', '.join(outside)}")
        return ToolRegistry(self._backend, names, self.max_output_chars)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            function_schema(
                name, TOOL_SPECS[name].description, TOOL_SPECS[name].arguments
            )
            for name in self.names
        ]

    async def execute(
        self, name: str, raw_arguments: str | dict[str, Any] | None, agent: str
    ) -> tuple[str, ToolCallRecord]:
        started = time.monotonic()
        try:
            arguments = parse_arguments(raw_arguments)
        except ValueError:
            return self._failure(
                agent,
                name,
                {},
                "invalid_arguments",
                "Arguments are not valid JSON",
                started,
            )
        if name not in self.names:
            return self._failure(
                agent,
                name,
                arguments,
                "tool_not_allowed",
                f"Tool '{name}' is not available to this agent. "
                f"Available: {', '.join(self.names)}",
                started,
            )
        try:
            validated = TOOL_SPECS[name].arguments.model_validate(arguments)
        except ValidationError as error:
            return self._failure(
                agent,
                name,
                arguments,
                "invalid_arguments",
                validation_summary(error),
                started,
            )
        try:
            result = await self._backend[name](**validated.model_dump())
            payload = result.model_dump(mode="json")
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._failure(
                agent,
                name,
                arguments,
                "tool_failure",
                "Tool execution failed",
                started,
            )
        payload = self._truncate(payload)
        record = ToolCallRecord(
            agent=agent,
            tool=name,
            arguments=validated.model_dump(exclude_none=True),
            status=payload.get("status", "error"),
            result=payload,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return json.dumps(payload, ensure_ascii=False), record

    def _truncate(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= self.max_output_chars:
            return payload
        data = dict(payload.get("data") or {})
        logs = data.get("logs")
        if isinstance(logs, str):
            # Keep the newest lines: they are the most relevant to an incident.
            overflow = len(text) - self.max_output_chars
            kept = logs[min(len(logs), overflow + 200) :]
            data["logs"] = kept[kept.find("\n") + 1 :] if "\n" in kept else kept
            data["truncated_by_agent"] = True
            payload = {**payload, "data": data}
            text = json.dumps(payload, ensure_ascii=False)
            if len(text) <= self.max_output_chars:
                return payload
        return {
            "tool": payload.get("tool"),
            "status": payload.get("status"),
            "data": {
                "truncated_by_agent": True,
                "preview": text[: self.max_output_chars],
            },
            "error": payload.get("error"),
        }

    @staticmethod
    def _failure(
        agent: str,
        name: str,
        arguments: dict[str, Any],
        kind: str,
        message: str,
        started: float,
    ) -> tuple[str, ToolCallRecord]:
        payload = {
            "tool": name,
            "status": "error",
            "data": {},
            "error": {"type": kind, "message": message},
        }
        record = ToolCallRecord(
            agent=agent,
            tool=name,
            arguments=arguments,
            status="error",
            result=payload,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return json.dumps(payload, ensure_ascii=False), record
