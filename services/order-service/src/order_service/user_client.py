import logging
from collections.abc import Iterator
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from pydantic import BaseModel, ValidationError

from order_service.config import get_user_service_timeout
from order_service.effective_config import effective_user_service_url
from order_service.observability import get_request_id, metrics

logger = logging.getLogger(__name__)


class UserNotFoundError(Exception):
    """Raised when User Service reports that a user does not exist."""


class UserServiceUnavailableError(Exception):
    """Raised when a connection to User Service cannot be established."""


class UserServiceTimeoutError(Exception):
    """Raised when User Service does not respond within the configured timeout."""


class UnexpectedUserServiceResponseError(Exception):
    """Raised when User Service violates its expected response contract."""


class UserReference(BaseModel):
    id: int


class UserServiceClient:
    def __init__(self, client: httpx.Client, target: str = "user-service") -> None:
        self._client = client
        self._target = target

    def ensure_user_exists(self, user_id: int) -> None:
        try:
            response = self._client.get(
                f"/users/{user_id}", headers={"X-Request-ID": get_request_id()}
            )
        except httpx.TimeoutException as error:
            metrics.record_dependency_timeout()
            logger.error(
                "User Service request timed out",
                extra={
                    "event": "dependency_connection_failed",
                    "target": self._target,
                    "exception_type": type(error).__name__,
                },
            )
            raise UserServiceTimeoutError from error
        except httpx.RequestError as error:
            metrics.record_dependency_error()
            logger.error(
                "User Service request failed",
                extra={
                    "event": "dependency_connection_failed",
                    "target": self._target,
                    "exception_type": type(error).__name__,
                },
            )
            raise UserServiceUnavailableError from error

        if response.status_code == 404:
            raise UserNotFoundError
        if response.status_code != 200:
            metrics.record_dependency_error()
            logger.error(
                "User Service returned an unexpected response",
                extra={
                    "event": "dependency_response_error",
                    "target": self._target,
                    "status_code": response.status_code,
                },
            )
            raise UnexpectedUserServiceResponseError

        try:
            user = UserReference.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            logger.error(
                "User Service returned an invalid response",
                extra={
                    "event": "dependency_response_error",
                    "target": self._target,
                    "status_code": response.status_code,
                    "exception_type": type(error).__name__,
                },
            )
            raise UnexpectedUserServiceResponseError from error
        if user.id != user_id:
            logger.error(
                "User Service returned a mismatched user",
                extra={
                    "event": "dependency_response_error",
                    "target": self._target,
                    "status_code": response.status_code,
                },
            )
            raise UnexpectedUserServiceResponseError


def get_user_service_client(request: Request) -> Iterator[UserServiceClient]:
    timeout = httpx.Timeout(get_user_service_timeout())
    url = effective_user_service_url(request)
    with httpx.Client(
        base_url=url,
        timeout=timeout,
    ) as client:
        parsed = urlsplit(url)
        yield UserServiceClient(client, f"{parsed.hostname}:{parsed.port}")
