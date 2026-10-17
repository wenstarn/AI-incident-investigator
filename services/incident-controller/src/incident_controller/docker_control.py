"""Restricted Docker Compose container operations."""

import os

import docker
from docker.errors import DockerException

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
ALLOWED_SERVICES = frozenset({"user-service", "redis"})


class DockerControlError(Exception):
    """A Docker operation failed or its target was unsafe or ambiguous."""


class DockerControl:
    def __init__(self, project: str) -> None:
        if not project or not project.strip():
            raise ValueError("Compose project name must not be empty")
        self.project = project

    def _find(self, client: docker.DockerClient, service: str):
        if service not in ALLOWED_SERVICES:
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
