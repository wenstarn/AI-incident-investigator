"""Scenario contract and the initial no-op scenario."""

from abc import ABC, abstractmethod
from collections.abc import Callable

from incident_controller.executor_client import ExecutorClient
from incident_controller.fault_client import FaultInjectionClient
from incident_controller.recovery import PostgresRecovery


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
        docker_control: ExecutorClient,
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
        if hasattr(self._docker, "wait_healthy"):
            self._docker.wait_healthy(
                self._service, expected_id=self._stopped_container_id
            )
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


class PostgresDownScenario(StopContainerScenario):
    def __init__(
        self, docker_control: ExecutorClient, recovery: PostgresRecovery
    ) -> None:
        super().__init__(
            "INC-003",
            "PostgreSQL Down",
            "Stops PostgreSQL in this Compose project.",
            "postgres",
            docker_control,
        )
        self._recovery = recovery

    def reset(self) -> None:
        if self._stopped_container_id is None:
            return
        super().reset()
        self._recovery.wait_until_ready(self._stopped_container_id)


class RuntimeFaultScenario(IncidentScenario):
    def __init__(
        self,
        incident_id: str,
        name: str,
        description: str,
        service: str,
        fault_type: str,
        parameters: dict[str, object],
        client_factory: Callable[[], FaultInjectionClient],
    ) -> None:
        self.id = incident_id
        self.name = name
        self.description = description
        self._service = service
        self._fault_type = fault_type
        self._parameters = parameters
        self._client_factory = client_factory
        self._fault_id: str | None = None

    def activate(self) -> None:
        with self._client_factory() as client:
            fault = client.activate_fault(
                self._service, self._fault_type, self._parameters, ttl_seconds=None
            )
        self._fault_id = fault["fault_id"]

    def verify(self) -> bool:
        if self._fault_id is None:
            return False
        with self._client_factory() as client:
            return any(
                fault.get("fault_id") == self._fault_id
                and fault.get("type") == self._fault_type
                and fault.get("parameters") == self._parameters
                for fault in client.get_active_faults(self._service)
            )

    def reset(self) -> None:
        if self._fault_id is None:
            return
        with self._client_factory() as client:
            client.remove_fault(self._service, self._fault_id)
