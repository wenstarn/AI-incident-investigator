"""Destructive Compose integration checks. Run only with make diagnostic-test."""

import asyncio
import os
import time

import httpx
from diagnostic_tools import (
    check_http_endpoint,
    check_postgres,
    check_redis,
    get_service_status,
)

BASE = os.getenv("INCIDENT_CONTROLLER_URL", "http://127.0.0.1:8003")
TOKEN = os.getenv("INCIDENT_CONTROLLER_TOKEN", "")


async def wait_for(probe, expected: str, seconds: int = 45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = await probe()
        if result.status == expected:
            return result
        await asyncio.sleep(1)
    raise AssertionError(f"Expected {expected}, got {result.model_dump_json()}")


async def main():
    if (
        not TOKEN
        or not os.getenv("DOCKER_EXECUTOR_READ_TOKEN")
        or not os.getenv("DIAGNOSTIC_POSTGRES_PASSWORD")
    ):
        raise RuntimeError(
            "Set incident, executor read, and diagnostic PostgreSQL credentials"
        )
    async with httpx.AsyncClient(timeout=15) as client:
        headers = {"X-Incident-Token": TOKEN}
        active = await client.get(f"{BASE}/incidents/active", headers=headers)
        active.raise_for_status()
        if active.json()["incident"] is not None:
            raise RuntimeError("Reset the existing incident before diagnostic-test")
        for probe in (
            lambda: check_http_endpoint("user-service", "/health"),
            lambda: check_http_endpoint("order-service", "/health"),
            check_postgres,
            check_redis,
        ):
            await wait_for(probe, "ok")
        for incident, probe, service in (
            (
                "INC-001",
                lambda: check_http_endpoint("user-service", "/health"),
                "user-service",
            ),
            ("INC-002", check_redis, "redis"),
            ("INC-003", check_postgres, "postgres"),
        ):
            activated = False
            try:
                response = await client.post(
                    f"{BASE}/incidents/{incident}/activate", headers=headers
                )
                response.raise_for_status()
                activated = True
                await wait_for(probe, "degraded")
                state = await wait_for(lambda: get_service_status(service), "degraded")
                assert state.data["container_status"] != "running"
                print(f"{incident}: passed")
            finally:
                if activated:
                    response = await client.post(
                        f"{BASE}/incidents/active/reset", headers=headers
                    )
                    response.raise_for_status()
                    await wait_for(probe, "ok", seconds=90)
    print("All diagnostic integration checks passed")


if __name__ == "__main__":
    asyncio.run(main())
