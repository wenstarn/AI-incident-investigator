"""Opt-in, destructive checks of INC-004 through INC-006."""

import asyncio
import os
import time

import httpx
from diagnostic_tools import (
    check_http_endpoint,
    check_redis,
    check_service_dependency,
    get_dependency_config,
    get_service_logs,
    get_service_metrics,
    get_service_status,
)

BASE = os.getenv("INCIDENT_CONTROLLER_URL", "http://127.0.0.1:8003")
TOKEN = os.getenv("INCIDENT_CONTROLLER_TOKEN", "")


async def wait_for(probe, expected: str, seconds: int = 30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = await probe()
        if result.status == expected:
            return result
        await asyncio.sleep(1)
    raise AssertionError(f"Expected {expected}, got {result.model_dump_json()}")


async def main() -> None:
    if not TOKEN or not os.getenv("DIAGNOSTICS_TOKEN"):
        raise RuntimeError("Set incident and diagnostics credentials")
    headers = {"X-Incident-Token": TOKEN}
    async with httpx.AsyncClient(timeout=15) as client:
        active = await client.get(f"{BASE}/incidents/active", headers=headers)
        active.raise_for_status()
        if active.json()["incident"] is not None:
            raise RuntimeError(
                "Reset the existing incident before advanced diagnostics"
            )
        for service in ("user-service", "order-service"):
            await wait_for(lambda service=service: get_service_status(service), "ok")
        assert (await get_service_metrics("order-service")).status == "ok"
        for incident in ("INC-004", "INC-005", "INC-006"):
            activated = False
            try:
                response = await client.post(
                    f"{BASE}/incidents/{incident}/activate", headers=headers
                )
                response.raise_for_status()
                activated = True
                await wait_for(lambda: get_service_status("user-service"), "ok")
                await wait_for(lambda: get_service_status("order-service"), "ok")
                if incident == "INC-004":
                    assert (
                        await check_http_endpoint("user-service", "/health")
                    ).status == "ok"
                    failed = await wait_for(
                        lambda: check_service_dependency(
                            "order-service", "user-service"
                        ),
                        "degraded",
                    )
                    assert failed.error.type == "connection_refused"
                    config = await get_dependency_config(
                        "order-service", "user-service"
                    )
                    assert config.status == "ok" and config.data["port"] == 9999
                elif incident == "INC-005":
                    direct = await check_http_endpoint(
                        "user-service", "/users/0", timeout_seconds=7
                    )
                    assert direct.data["reachable"] is True
                    assert direct.data["latency_ms"] >= 4500
                    failed = await wait_for(
                        lambda: check_service_dependency(
                            "order-service", "user-service"
                        ),
                        "degraded",
                    )
                    assert failed.error.type == "timeout"
                    order = await client.post(
                        os.environ["ORDER_SERVICE_URL"] + "/orders",
                        json={"user_id": 1, "product_id": 1, "quantity": 1},
                        timeout=8,
                    )
                    assert order.status_code == 504
                    measured = await get_service_metrics("order-service")
                    assert measured.status == "ok"
                    assert measured.data["metrics"]["dependency_timeouts_total"] >= 1
                    logs = await get_service_logs(
                        "order-service", tail=200, since_seconds=120
                    )
                    assert logs.status == "ok"
                    assert "dependency_connection_failed" in logs.data["logs"]
                else:
                    assert (await check_redis()).status == "ok"
                    assert (await get_service_status("redis")).status == "ok"
                    failed = await wait_for(
                        lambda: check_service_dependency("order-service", "redis"),
                        "degraded",
                    )
                    assert failed.error.type == "connection_refused"
                    config = await get_dependency_config("order-service", "redis")
                    assert config.status == "ok" and config.data["port"] == 9999
                print(f"{incident}: passed")
            finally:
                if activated:
                    reset = await client.post(
                        f"{BASE}/incidents/active/reset", headers=headers
                    )
                    reset.raise_for_status()
                    dependency = "redis" if incident == "INC-006" else "user-service"
                    await wait_for(
                        lambda: check_service_dependency("order-service", dependency),
                        "ok",
                        seconds=45,
                    )
                    if incident == "INC-005":
                        direct = await check_http_endpoint(
                            "user-service", "/users/0", timeout_seconds=7
                        )
                        assert direct.data["latency_ms"] < 2000
    print("All advanced diagnostic integration checks passed")


if __name__ == "__main__":
    asyncio.run(main())
