"""Restricted HTTP client for the internal Docker executor."""

import os

import httpx

from incident_controller.errors import DockerControlError


class ExecutorClient:
    def __init__(self, url: str, token: str, timeout: float = 15) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _call(self, operation: str, service: str, expected_id: str | None = None):
        if not self._token:
            raise DockerControlError("Docker executor token is not configured")
        try:
            response = httpx.post(
                f"{self._url}/operations/{operation}",
                json={"service": service, "expected_id": expected_id},
                headers={"X-Executor-Token": self._token},
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise DockerControlError("Docker executor request failed") from error

    def stop(self, service: str) -> str:
        return self._call("stop", service)["container_id"]

    def start(self, service: str, *, expected_id: str) -> None:
        self._call("start", service, expected_id)

    def status(self, service: str, *, expected_id: str | None = None) -> str:
        return self._call("status", service, expected_id)["status"]

    def health_status(self, service: str, *, expected_id: str) -> str:
        return self._call("health", service, expected_id)["status"]

    def wait_healthy(self, service: str, *, expected_id: str) -> None:
        self._call("wait-healthy", service, expected_id)


def create_executor_client() -> ExecutorClient:
    return ExecutorClient(
        os.getenv("DOCKER_EXECUTOR_URL", "http://docker-executor:8004"),
        os.getenv("DOCKER_EXECUTOR_TOKEN", ""),
        float(os.getenv("DOCKER_EXECUTOR_TIMEOUT_SECONDS", "90")),
    )
