"""Scenario contract and the initial no-op scenario."""

from abc import ABC, abstractmethod

from incident_controller.docker_control import DockerControl


class IncidentScenario(ABC):
    id: str
    name: str
    description: str

    @abstractmethod
    def activate(self) -> None:
        """Apply the scenario."""

    @abstractmethod
    def reset(self) -> None:
        """Undo the scenario."""

    @abstractmethod
    def verify(self) -> bool:
        """Report whether the scenario is active."""


class DummyScenario(IncidentScenario):
    id = "dummy"
    name = "Dummy incident"
    description = "A test incident that does not affect the application."

    def __init__(self) -> None:
        self._active = False

    def activate(self) -> None:
        self._active = True

    def reset(self) -> None:
        self._active = False

    def verify(self) -> bool:
        return self._active


class StopContainerScenario(IncidentScenario):
    def __init__(
        self,
        incident_id: str,
        name: str,
        description: str,
        service: str,
        docker_control: DockerControl,
    ) -> None:
        self.id = incident_id
        self.name = name
        self.description = description
        self._service = service
        self._docker = docker_control
        self._stopped_container_id: str | None = None

    def activate(self) -> None:
        self._stopped_container_id = None
        self._stopped_container_id = self._docker.stop(self._service)

    def reset(self) -> None:
        if self._stopped_container_id is None:
            return
        self._docker.start(self._service, expected_id=self._stopped_container_id)
        if (
            self._docker.status(self._service, expected_id=self._stopped_container_id)
            != "running"
        ):
            raise RuntimeError("Container did not reach running state")

    def verify(self) -> bool:
        if self._stopped_container_id is None:
            return False
        return (
            self._docker.status(self._service, expected_id=self._stopped_container_id)
            == "exited"
        )
