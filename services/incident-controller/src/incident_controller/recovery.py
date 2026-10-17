"""Bounded PostgreSQL and service database health verification."""

import json
import os
import time
from urllib.error import URLError
from urllib.request import urlopen

from incident_controller.errors import DockerControlError
from incident_controller.executor_client import ExecutorClient


class RecoveryError(DockerControlError):
    """PostgreSQL or dependent service did not recover in time."""


class PostgresRecovery:
    def __init__(
        self,
        docker_control: ExecutorClient,
        health_urls: tuple[str, str],
        timeout_seconds: float,
        request_timeout_seconds: float,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if (
            timeout_seconds <= 0
            or request_timeout_seconds <= 0
            or poll_interval_seconds <= 0
        ):
            raise ValueError("Recovery timeouts must be positive")
        self._docker = docker_control
        self._health_urls = health_urls
        self._timeout_seconds = timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def _endpoint_healthy(self, url: str, timeout_seconds: float) -> bool:
        try:
            with urlopen(url, timeout=timeout_seconds) as response:
                body = json.load(response)
                return (
                    response.status == 200
                    and isinstance(body, dict)
                    and body.get("status") == "ok"
                )
        except (OSError, URLError, ValueError):
            return False

    def wait_until_ready(self, container_id: str) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            if (
                self._docker.health_status("postgres", expected_id=container_id)
                == "healthy"
            ):
                endpoints_healthy = True
                for url in self._health_urls:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RecoveryError("PostgreSQL recovery timed out")
                    if not self._endpoint_healthy(
                        url, min(self._request_timeout_seconds, remaining)
                    ):
                        endpoints_healthy = False
                if endpoints_healthy:
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RecoveryError("PostgreSQL recovery timed out")
            time.sleep(min(self._poll_interval_seconds, remaining))


def get_postgres_recovery(
    docker_control: ExecutorClient,
) -> PostgresRecovery:
    return PostgresRecovery(
        docker_control,
        (
            os.environ.get(
                "INCIDENT_USER_DB_HEALTH_URL", "http://127.0.0.1:8001/health/db"
            ),
            os.environ.get(
                "INCIDENT_ORDER_DB_HEALTH_URL", "http://127.0.0.1:8002/health/db"
            ),
        ),
        float(os.environ.get("INCIDENT_POSTGRES_RECOVERY_TIMEOUT_SECONDS", "60")),
        float(os.environ.get("INCIDENT_HEALTH_REQUEST_TIMEOUT_SECONDS", "2")),
    )
