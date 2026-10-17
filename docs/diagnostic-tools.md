# Diagnostic tools

`services/diagnostic-tools` is an installable, serverless Python package. Its eight async functions observe the shop through allowlisted HTTP GET probes, direct PostgreSQL `SELECT 1`, Redis `PING`, and read-only Docker Executor endpoints. They never import or call Incident Controller. They do not use Docker socket access.

## Result contract

Every call returns a Pydantic `DiagnosticResult` with `tool`, `status`, `data`, and `error` (`null` or `{type, message}`). `model_dump_json()` serializes it. `ok` means the probe completed and found a healthy target. `degraded` means the probe completed and observed a fault, including a refused connection or timeout. `error` means invalid input, missing credentials, denied executor access, or an unusable diagnostic response. Error messages omit dependency exception details and credentials.

## Functions

- `get_service_status(service)`: allowlisted Compose container state, health and start time.
- `get_service_logs(service, tail=50, since_seconds=None)`: timestamped bounded text, 1–200 lines and optional 1–86400 second window. Response is at most 60 KB and reports truncation. Lines containing secret-related field names are omitted by the executor.
- `check_http_endpoint(service, path, timeout_seconds=None)`: only GET to User Service `/health`, `/health/db`, `/users/0` or Order Service `/health`, `/health/db`, `/health/redis`. Redirects are not followed. Response limit is 8 KB.
- `check_postgres()`: direct `SELECT 1` in read-only transaction.
- `check_redis()`: direct `PING`.
- `get_service_metrics(service)`: allowlisted current counters from the existing `/internal/stats` endpoint.
- `check_service_dependency(source_service, dependency)`: probe from Order Service using its effective dependency address.
- `get_dependency_config(service, dependency)`: safe host, port and timeout currently used by Order Service.

Status and logs accept `user-service`, `order-service`, `postgres`, `redis`, `frontend`. HTTP accepts only the two business services. Unknown targets return `error`.

## Configuration

Set `USER_SERVICE_URL`, `ORDER_SERVICE_URL`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `REDIS_HOST`, `REDIS_PORT`, `DIAGNOSTIC_POSTGRES_USER`, `DIAGNOSTIC_POSTGRES_PASSWORD`, `DOCKER_EXECUTOR_READ_URL`, `DOCKER_EXECUTOR_READ_TOKEN`, `DIAGNOSTICS_TOKEN`. Defaults use localhost ports 8001, 8002, 5432, 6379, and 8005. In a Compose agent container use `http://user-service:8001`, `http://order-service:8002`, `postgres`, `redis`, and `http://docker-executor:8004`. The agent container must join the executor network for status/log access, without mounting the Docker socket. Never give it `DOCKER_EXECUTOR_TOKEN` or `INCIDENT_CONTROLLER_TOKEN`.

Timeouts: `DIAGNOSTIC_HTTP_TIMEOUT_SECONDS=5`, `DIAGNOSTIC_POSTGRES_TIMEOUT_SECONDS=3`, `DIAGNOSTIC_REDIS_TIMEOUT_SECONDS=3`, `DIAGNOSTIC_DOCKER_TIMEOUT_SECONDS=5`.

Create distinct random `DOCKER_EXECUTOR_READ_TOKEN`, `DIAGNOSTICS_TOKEN`, and `DIAGNOSTIC_POSTGRES_PASSWORD` in `.env`. Compose's `diagnostic-db-init` provisions a role with login and database connect only, including on an existing volume. Re-run that one-shot service after changing the reader password. Its role has no table privileges. Executor rejects a read token equal to its privileged token. A localhost-only Nginx proxy exposes only `/read-only/services/...` GET routes on port 8005. It and Docker Executor share a dedicated `diagnostic_access` network. The privileged operations have no host-published port, and the proxy does not forward their paths.

## Usage

```sh
uv sync --all-packages
uv run --package diagnostic-tools python examples/diagnostic_tools_demo.py
uv run pytest services/diagnostic-tools/tests
make diagnostic-test
make diagnostic-test-advanced
```

```python
import asyncio
from diagnostic_tools import get_service_status


async def main():
    print((await get_service_status("user-service")).model_dump_json())


asyncio.run(main())
```

The standalone demo reads the repository `.env` for its allowlisted diagnostic settings when they are not already in the process environment. The `diagnostic_tools` library itself does not load `.env`; applications importing it must pass settings through environment variables (for example, `uv run --env-file .env --package diagnostic-tools python your_script.py`). The demo exits with a list of missing credential names if its three read-only credentials are unset.

`make diagnostic-test` runs in a temporary diagnostic test container, changes container state through Incident Controller strictly in test orchestration, and resets in `finally`. Keep it separate from ordinary `make test`. The database role provisioning requires a nonempty diagnostic password. Docker must be running for the integration command.

## Interaction diagnostics

### `get_service_metrics(service)`

Accepts `user-service` or `order-service`. It reads the existing `GET /internal/stats` response and returns only actual counters in `data.metrics`: `requests_total`, `errors_total`, `cache_hits`, `cache_misses`, `dependency_timeouts_total`, `dependency_errors_total`, `redis_connection_errors_total`, and selected `database_pool` values when present. Missing counters are omitted. These are process-lifetime counters and reset on restart. HTTP request latency is in JSON logs, not a metrics aggregate. `status=error` means invalid service, unavailable endpoint, timeout, or unusable response. It sends no token or write request.

```python
result = await get_service_metrics("order-service")
# {"tool":"get_service_metrics","status":"ok","data":{"service":"order-service","metrics":{"requests_total":12,"cache_hits":3}},"error":null}
```

### `check_service_dependency(source_service, dependency)`

Accepts only `order-service` as source and `user-service` or `redis` as dependency. Calls the fixed authenticated Order Service route `GET /internal/diagnostics/dependencies/{dependency}`. Order Service probes User Service using `GET /users/0` with its existing 2-second business timeout, or Redis using `PING` with its existing Redis timeout. The endpoint uses the same effective address selection as business operations, including runtime overrides. A User Service 404 means the network path worked: no user is created. `data` contains source, dependency, reachability, latency, and an HTTP status when applicable. A completed failed probe is `degraded` with `timeout` or `connection_refused`; invalid arguments, missing token, denied access, or Order Service failure are `error`. No request-supplied URL or write method is accepted.

```python
result = await check_service_dependency("order-service", "user-service")
# {"tool":"check_service_dependency","status":"degraded","data":{"source_service":"order-service","dependency":"user-service","reachable":false,"latency_ms":2000},"error":{"type":"timeout","message":"Dependency probe failed"}}
```

### `get_dependency_config(service, dependency)`

Accepts the same pair. Calls the fixed authenticated `GET /internal/diagnostics/config/{dependency}` route. `data` contains only service, dependency, effective host, port, and timeout. It omits usernames, passwords, complete URLs, all environment variables, fault state, and incident IDs. An invalid pair, rejected token, or unavailable service returns `error`. The effective configuration is observed from Order Service at call time.

```python
result = await get_dependency_config("order-service", "redis")
# {"tool":"get_dependency_config","status":"ok","data":{"service":"order-service","dependency":"redis","host":"redis","port":9999,"timeout_seconds":1.0},"error":null}
```

### Slow User Service

`check_http_endpoint("user-service", "/users/0", timeout_seconds=7)` permits an explicit bounded timeout of at most 15 seconds. It measures the complete response and can observe the 5-second delay in INC-005 without inserting a user. A 404 still includes `reachable=true` and `latency_ms`; its `status` is `degraded` because it is not a 2xx HTTP result. The Order Service dependency probe uses the unchanged business timeout and therefore reports a timeout during the same incident.

The new Order Service routes require a distinct `DIAGNOSTICS_TOKEN` header. They are excluded from OpenAPI, are not routed through the frontend, and accept GET only. An unset token disables them. The fault-management route rejects a token shared with `DIAGNOSTICS_TOKEN`. The diagnostic package never reads Fault Registry or calls Incident Controller; only the opt-in integration test runner uses Incident Controller to activate and reset scenarios.

`make diagnostic-test-advanced` checks INC-004, INC-005, and INC-006 in sequence, with reset in `finally` and restoration checks. It is not part of `make test`.
