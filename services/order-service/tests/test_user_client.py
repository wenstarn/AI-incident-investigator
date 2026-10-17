import httpx
import pytest
from fastapi import Request
from fault_injection.models import FaultCreate
from order_service.main import app
from order_service.observability import reset_request_id, set_request_id
from order_service.user_client import (
    UnexpectedUserServiceResponseError,
    UserNotFoundError,
    UserServiceClient,
    UserServiceTimeoutError,
    UserServiceUnavailableError,
    get_user_service_client,
)


def test_runtime_user_service_url_override(monkeypatch) -> None:
    monkeypatch.setenv("USER_SERVICE_URL", "http://user-service:8001")
    fault = app.state.fault_registry.set_fault(
        FaultCreate(
            type="user_service_url_override",
            parameters={"url": "http://user-service:9999"},
        )
    )
    request = Request({"type": "http", "app": app})
    try:
        dependency = get_user_service_client(request)
        try:
            client = next(dependency)
            assert str(client._client.base_url) == "http://user-service:9999"
        finally:
            dependency.close()
    finally:
        app.state.fault_registry.remove_fault(fault.fault_id)
    dependency = get_user_service_client(request)
    try:
        client = next(dependency)
        assert str(client._client.base_url) == "http://user-service:8001"
    finally:
        dependency.close()


def make_client(handler: httpx.MockTransport) -> UserServiceClient:
    return UserServiceClient(
        httpx.Client(base_url="http://user-service.test", transport=handler)
    )


def test_user_exists() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"id": 7}, request=request)
    )
    make_client(transport).ensure_user_exists(7)


def test_request_id_is_forwarded() -> None:
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"id": 7}, request=request)

    token = set_request_id("trace-between-services")
    try:
        make_client(httpx.MockTransport(handler)).ensure_user_exists(7)
    finally:
        reset_request_id(token)

    assert captured_request is not None
    assert captured_request.headers["X-Request-ID"] == "trace-between-services"


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (httpx.Response(404), UserNotFoundError),
        (httpx.Response(500), UnexpectedUserServiceResponseError),
        (
            httpx.Response(200, json={"name": "missing id"}),
            UnexpectedUserServiceResponseError,
        ),
        (httpx.Response(200, json={"id": 999}), UnexpectedUserServiceResponseError),
    ],
)
def test_response_errors(
    response: httpx.Response, expected_error: type[Exception]
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            response.status_code,
            content=response.content,
            headers=response.headers,
            request=request,
        )
    )
    with pytest.raises(expected_error):
        make_client(transport).ensure_user_exists(7)


@pytest.mark.parametrize(
    ("error_type", "expected_error"),
    [
        (httpx.ConnectError, UserServiceUnavailableError),
        (httpx.ReadTimeout, UserServiceTimeoutError),
    ],
)
def test_transport_errors(
    error_type: type[httpx.RequestError], expected_error: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("failed", request=request)

    with pytest.raises(expected_error):
        make_client(httpx.MockTransport(handler)).ensure_user_exists(7)
