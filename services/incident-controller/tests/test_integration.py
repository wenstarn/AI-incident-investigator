"""Opt-in destructive tests against the running local Compose stack."""

import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.destructive
BASE = "http://127.0.0.1:8003"


def request(method, path, **kwargs):
    token = os.environ["INCIDENT_CONTROLLER_TOKEN"]
    with httpx.Client(timeout=100, headers={"X-Incident-Token": token}) as client:
        response = client.request(method, BASE + path, **kwargs)
        response.raise_for_status()
        return response.json()


def test_all_incidents_recover():
    if not os.getenv("INCIDENT_CONTROLLER_TOKEN"):
        pytest.skip("Set INCIDENT_CONTROLLER_TOKEN and start Compose first")
    assert httpx.get(BASE + "/health").status_code == 200
    ids = {item["id"] for item in request("GET", "/incidents")}
    assert {f"INC-{i:03}" for i in range(1, 7)} <= ids
    request("POST", "/incidents/active/reset")
    user = httpx.post(
        "http://127.0.0.1:8001/users",
        json={
            "name": "Incident test",
            "email": f"incident-{uuid.uuid4()}@example.com",
        },
        timeout=5,
    )
    user.raise_for_status()
    user_id = user.json()["id"]
    for incident_id in sorted(ids - {"dummy"}):
        try:
            active = request("POST", f"/incidents/{incident_id}/activate")
            assert active["incident"]["id"] == incident_id
            with httpx.Client(
                timeout=10,
                headers={"X-Incident-Token": os.environ["INCIDENT_CONTROLLER_TOKEN"]},
            ) as client:
                assert (
                    client.post(BASE + f"/incidents/{incident_id}/activate").status_code
                    == 409
                )
            if incident_id in {"INC-004", "INC-005", "INC-006"}:
                service_port = 8001 if incident_id == "INC-005" else 8002
                fault_response = httpx.get(
                    f"http://127.0.0.1:{service_port}/internal/faults",
                    headers={"X-Fault-Token": os.environ["FAULT_INJECTION_TOKEN"]},
                    timeout=5,
                )
                fault_response.raise_for_status()
                assert len(fault_response.json()) == 1
                assert fault_response.json()[0]["expires_at"] is None
            if incident_id == "INC-001":
                assert not _available("http://127.0.0.1:8001/health")
            elif incident_id == "INC-002":
                assert not _redis_healthy()
            elif incident_id == "INC-003":
                assert not _available("http://127.0.0.1:8002/health/db")
            elif incident_id in {"INC-004", "INC-005"}:
                response = httpx.post(
                    "http://127.0.0.1:8002/orders",
                    json={"user_id": user_id, "product_id": 1, "quantity": 1},
                    timeout=10,
                )
                assert response.status_code == (
                    502 if incident_id == "INC-004" else 504
                )
            else:
                assert (
                    httpx.get("http://127.0.0.1:8002/products/1", timeout=5).status_code
                    == 200
                )
                assert _redis_healthy()
                faults = httpx.get(
                    "http://127.0.0.1:8002/internal/faults",
                    headers={"X-Fault-Token": os.environ["FAULT_INJECTION_TOKEN"]},
                    timeout=5,
                ).json()
                assert any(fault["type"] == "redis_url_override" for fault in faults)
        finally:
            request("POST", "/incidents/active/reset")
            request("POST", "/incidents/active/reset")
            assert request("GET", "/incidents/active") == {"incident": None}
            if incident_id in {"INC-004", "INC-005", "INC-006"}:
                service_port = 8001 if incident_id == "INC-005" else 8002
                response = httpx.get(
                    f"http://127.0.0.1:{service_port}/internal/faults",
                    headers={"X-Fault-Token": os.environ["FAULT_INJECTION_TOKEN"]},
                    timeout=5,
                )
                response.raise_for_status()
                assert response.json() == []
        if incident_id == "INC-001":
            assert _available("http://127.0.0.1:8001/health")
        if incident_id == "INC-002":
            assert _redis_healthy()
        if incident_id == "INC-003":
            assert _available("http://127.0.0.1:8002/health/db")


def _available(url):
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.RequestError:
        return False


def _redis_healthy():
    return _available("http://127.0.0.1:8002/health/redis")
