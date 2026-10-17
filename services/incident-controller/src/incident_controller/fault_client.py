"""HTTP client for the services' protected runtime fault APIs."""

import os
from typing import Any, Literal
from urllib.parse import quote

import httpx


class FaultClientError(Exception):
    """A service fault API failed or returned an invalid response."""


class FaultClientForbiddenError(FaultClientError):
    pass


class FaultClientConflictError(FaultClientError):
    pass


class FaultClientUnavailableError(FaultClientError):
    pass


class FaultClientTimeoutError(FaultClientError):
    pass


class FaultClientConfigurationError(FaultClientError):
    """The controller cannot authenticate to internal fault APIs."""


class FaultInjectionClient:
    def __init__(
        self,
        user_service_url: str,
        order_service_url: str,
        token: str,
        timeout_seconds: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token or timeout_seconds <= 0:
            raise ValueError("Fault token and positive timeout are required")
        self._urls = {
            "user-service": user_service_url.rstrip("/"),
            "order-service": order_service_url.rstrip("/"),
        }
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={"X-Fault-Token": token},
            transport=transport,
        )

    def __enter__(self) -> "FaultInjectionClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        service: Literal["user-service", "order-service"],
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            base = self._urls[service]
        except KeyError as error:
            raise ValueError("Unknown service") from error
        try:
            response = self._client.request(method, base + path, **kwargs)
        except httpx.TimeoutException as error:
            raise FaultClientTimeoutError("Fault service timed out") from error
        except httpx.RequestError as error:
            raise FaultClientUnavailableError("Fault service unavailable") from error
        if response.status_code == 403:
            raise FaultClientForbiddenError("Fault service rejected credentials")
        if response.status_code == 409:
            raise FaultClientConflictError("Fault already active")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise FaultClientError("Fault service returned an error") from error
        return response

    @staticmethod
    def _json(response: httpx.Response, expected: type) -> Any:
        try:
            body = response.json()
        except ValueError as error:
            raise FaultClientError("Invalid fault service response") from error
        if not isinstance(body, expected):
            raise FaultClientError("Invalid fault service response")
        return body

    def activate_fault(
        self,
        service: Literal["user-service", "order-service"],
        fault_type: str,
        parameters: dict[str, Any],
        ttl_seconds: int | None = 300,
    ) -> dict[str, Any]:
        response = self._request(
            service,
            "POST",
            "/internal/faults",
            json={
                "type": fault_type,
                "parameters": parameters,
                "ttl_seconds": ttl_seconds,
            },
        )
        body = self._json(response, dict)
        if not all(key in body for key in ("fault_id", "type", "expires_at")):
            raise FaultClientError("Invalid fault service response")
        return body

    def get_active_faults(
        self, service: Literal["user-service", "order-service"]
    ) -> list[dict[str, Any]]:
        body = self._json(self._request(service, "GET", "/internal/faults"), list)
        if not all(isinstance(item, dict) for item in body):
            raise FaultClientError("Invalid fault service response")
        return body

    def remove_fault(
        self, service: Literal["user-service", "order-service"], fault_id: str
    ) -> None:
        self._request(service, "DELETE", f"/internal/faults/{quote(fault_id, safe='')}")

    def clear_faults(self, service: Literal["user-service", "order-service"]) -> None:
        self._request(service, "DELETE", "/internal/faults")


def create_fault_client() -> FaultInjectionClient:
    token = os.getenv("FAULT_INJECTION_TOKEN", "")
    if not token:
        raise FaultClientConfigurationError(
            "FAULT_INJECTION_TOKEN is not configured in Incident Controller"
        )
    return FaultInjectionClient(
        os.getenv("USER_SERVICE_INTERNAL_URL", "http://127.0.0.1:8001"),
        os.getenv("ORDER_SERVICE_INTERNAL_URL", "http://127.0.0.1:8002"),
        token,
        float(os.getenv("FAULT_INJECTION_TIMEOUT_SECONDS", "2")),
    )
