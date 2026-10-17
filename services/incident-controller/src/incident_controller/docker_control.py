"""Restricted Docker Compose container operations."""

import os
import re
import time

import docker
from docker.errors import DockerException

from incident_controller.errors import DockerControlError

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
ALLOWED_SERVICES = frozenset({"user-service", "redis", "postgres"})
READABLE_SERVICES = ALLOWED_SERVICES | {"order-service", "frontend"}
SECRET_LINE = re.compile(
    r"(?i)(password|token|secret|credential|authorization|api[_-]?key|"
    r"(?:postgres(?:ql)?|redis|https?)://[^\s]+:[^\s]+@)"
)


class DockerControl:
    def __init__(self, project: str) -> None:
        if not project or not project.strip():
            raise ValueError("Compose project name must not be empty")
        self.project = project

    def _find(
        self, client: docker.DockerClient, service: str, *, readonly: bool = False
    ):
        if service not in (READABLE_SERVICES if readonly else ALLOWED_SERVICES):
            raise DockerControlError("Service is not allowed")
        containers = client.containers.list(
            all=True,
            filters={
                "label": [
                    f"{PROJECT_LABEL}={self.project}",
                    f"{SERVICE_LABEL}={service}",
                ]
            },
        )
        if len(containers) != 1:
            raise DockerControlError("Expected exactly one matching container")
        container = containers[0]
        # Recheck the returned object; the filter alone is not an authorization check.
        container.reload()
        labels = container.attrs.get("Config", {}).get("Labels") or {}
        if (
            labels.get(PROJECT_LABEL) != self.project
            or labels.get(SERVICE_LABEL) != service
        ):
            raise DockerControlError("Container labels do not match the target")
        return container

    def inspect_readonly(self, service: str) -> dict:
        try:
            client = docker.from_env()
            try:
                container = self._find(client, service, readonly=True)
                state = container.attrs.get("State", {})
                return {
                    "container_status": state.get("Status", container.status),
                    "health_status": (state.get("Health") or {}).get("Status"),
                    "started_at": state.get("StartedAt"),
                }
            finally:
                client.close()
        except DockerException as error:
            raise DockerControlError("Docker API request failed") from error

    def logs_readonly(
        self, service: str, *, tail: int, since_seconds: int | None
    ) -> dict:
        try:
            client = docker.from_env()
            try:
                container = self._find(client, service, readonly=True)
                kwargs = {"tail": tail, "timestamps": True, "stream": False}
                if since_seconds is not None:
                    kwargs["since"] = int(time.time()) - since_seconds
                raw = container.logs(**kwargs)
                lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
                # Remove lines that might contain secrets before returning diagnostics.
                safe = [line for line in lines if not SECRET_LINE.search(line)]
                kept = []
                size = 0
                for line in reversed(safe):
                    line_size = len(line.encode("utf-8"))
                    if size + line_size > 60000:
                        break
                    kept.append(line)
                    size += line_size
                truncated = len(kept) < len(safe)
                joined = "".join(reversed(kept))
                return {
                    "logs": joined,
                    "truncated": truncated,
                    "container_status": container.status,
                }
            finally:
                client.close()
        except DockerException as error:
            raise DockerControlError("Docker API request failed") from error

    def status(self, service: str, *, expected_id: str | None = None) -> str:
        try:
            client = docker.from_env()
            try:
                container = self._find(client, service)
                if expected_id is not None and container.id != expected_id:
                    raise DockerControlError("Target container has changed")
                return container.status
            finally:
                client.close()
        except DockerException as error:
            raise DockerControlError("Docker API request failed") from error

    def health_status(self, service: str, *, expected_id: str) -> str:
        try:
            client = docker.from_env()
            try:
                container = self._find(client, service)
                if container.id != expected_id:
                    raise DockerControlError("Target container has changed")
                health = container.attrs.get("State", {}).get("Health")
                if not health or not health.get("Status"):
                    raise DockerControlError("Target container has no healthcheck")
                return health["Status"]
            finally:
                client.close()
        except DockerException as error:
            raise DockerControlError("Docker API request failed") from error

    def stop(self, service: str) -> str:
        try:
            client = docker.from_env()
            try:
                container = self._find(client, service)
                if container.status != "running":
                    raise DockerControlError("Target container is not running")
                container_id = container.id
                container.stop(timeout=10)
                return container_id
            finally:
                client.close()
        except DockerException as error:
            raise DockerControlError("Docker API request failed") from error

    def start(self, service: str, *, expected_id: str) -> None:
        try:
            client = docker.from_env()
            try:
                container = self._find(client, service)
                if container.id != expected_id:
                    raise DockerControlError("Target container has changed")
                if container.status != "running":
                    container.start()
            finally:
                client.close()
        except DockerException as error:
            raise DockerControlError("Docker API request failed") from error


def get_compose_project() -> str:
    return os.environ.get(
        "INCIDENT_COMPOSE_PROJECT",
        os.environ.get("COMPOSE_PROJECT_NAME", "ai-incident-investigator"),
    )
