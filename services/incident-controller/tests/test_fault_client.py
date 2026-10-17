import httpx
import pytest

from incident_controller.fault_client import (
    FaultClientConflictError,
    FaultClientError,
    FaultClientForbiddenError,
    FaultClientTimeoutError,
    FaultClientUnavailableError,
    FaultInjectionClient,
)


def client(handler):
    return FaultInjectionClient(
        "http://localhost:8001",
        "http://localhost:8002",
        "test-token",
        transport=httpx.MockTransport(handler),
    )


def test_create_list_delete_and_clear() -> None:
    def handler(request):
        assert request.headers["X-Fault-Token"] == "test-token"
        if request.method == "POST":
            return httpx.Response(
                201,
                json={"fault_id": "f1", "type": "request_delay", "expires_at": "now"},
            )
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(204)

    with client(handler) as api:
        assert (
            api.activate_fault("user-service", "request_delay", {"delay_ms": 1})[
                "fault_id"
            ]
            == "f1"
        )
        assert api.get_active_faults("order-service") == []
        api.remove_fault("user-service", "f1")
        api.clear_faults("order-service")


@pytest.mark.parametrize(
    "status,error", [(403, FaultClientForbiddenError), (409, FaultClientConflictError)]
)
def test_http_failures(status, error) -> None:
    with client(lambda _: httpx.Response(status)) as api:
        with pytest.raises(error):
            api.activate_fault("user-service", "request_delay", {})


@pytest.mark.parametrize(
    "failure,error",
    [
        (httpx.ConnectError("refused"), FaultClientUnavailableError),
        (httpx.ReadTimeout("timed out"), FaultClientTimeoutError),
    ],
)
def test_network_failures(failure, error) -> None:
    def handler(request):
        raise failure

    with client(handler) as api:
        with pytest.raises(error):
            api.get_active_faults("user-service")


def test_invalid_response() -> None:
    with client(lambda _: httpx.Response(201, json={"unexpected": True})) as api:
        with pytest.raises(FaultClientError):
            api.activate_fault("user-service", "request_delay", {})


def test_client_can_request_fault_without_expiry():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            201,
            json={"fault_id": "f1", "type": "request_delay", "expires_at": None},
        )

    with FaultInjectionClient(
        "http://user-service:8001",
        "http://order-service:8002",
        "test-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.activate_fault(
            "user-service", "request_delay", {"delay_ms": 5000}, ttl_seconds=None
        )
    assert response["expires_at"] is None
    import json

    assert json.loads(requests[0].content)["ttl_seconds"] is None
