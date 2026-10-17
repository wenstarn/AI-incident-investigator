"""In-process scenario catalog and lifecycle management."""

from threading import Lock

from incident_controller.docker_control import (
    DockerControl,
    DockerControlError,
    get_compose_project,
)
from incident_controller.scenarios import (
    DummyScenario,
    IncidentScenario,
    StopContainerScenario,
)


class IncidentNotFoundError(Exception):
    pass


class IncidentAlreadyActiveError(Exception):
    pass


class IncidentOperationError(Exception):
    pass


class IncidentDependencyError(IncidentOperationError):
    pass


class IncidentController:
    def __init__(self, scenarios: list[IncidentScenario]) -> None:
        self._scenarios = {scenario.id: scenario for scenario in scenarios}
        if len(self._scenarios) != len(scenarios):
            raise ValueError("Scenario IDs must be unique")
        self._active_id: str | None = None
        self._lock = Lock()

    def list_incidents(self) -> list[IncidentScenario]:
        return list(self._scenarios.values())

    def active_incident(self) -> IncidentScenario | None:
        with self._lock:
            if self._active_id is None:
                return None
            return self._scenarios[self._active_id]

    def activate(self, incident_id: str) -> IncidentScenario:
        with self._lock:
            scenario = self._scenarios.get(incident_id)
            if scenario is None:
                raise IncidentNotFoundError(incident_id)
            if self._active_id is not None:
                raise IncidentAlreadyActiveError(self._active_id)
            try:
                scenario.activate()
                if not scenario.verify():
                    raise IncidentOperationError("Scenario verification failed")
            except Exception as error:
                try:
                    scenario.reset()
                except Exception as reset_error:
                    self._active_id = incident_id
                    error_class = (
                        IncidentDependencyError
                        if isinstance(reset_error, DockerControlError)
                        else IncidentOperationError
                    )
                    raise error_class(
                        "Scenario activation and rollback failed"
                    ) from reset_error
                error_class = (
                    IncidentDependencyError
                    if isinstance(error, DockerControlError)
                    else IncidentOperationError
                )
                raise error_class("Scenario activation failed") from error
            self._active_id = incident_id
            return scenario

    def reset(self) -> None:
        with self._lock:
            if self._active_id is None:
                return
            scenario = self._scenarios[self._active_id]
            try:
                scenario.reset()
                if scenario.verify():
                    raise IncidentOperationError("Scenario reset verification failed")
            except Exception as error:
                error_class = (
                    IncidentDependencyError
                    if isinstance(error, DockerControlError)
                    else IncidentOperationError
                )
                raise error_class("Scenario reset failed") from error
            self._active_id = None


docker_control = DockerControl(get_compose_project())
controller = IncidentController(
    [
        DummyScenario(),
        StopContainerScenario(
            "INC-001",
            "Stop User Service",
            "Stops the User Service container in this Compose project.",
            "user-service",
            docker_control,
        ),
        StopContainerScenario(
            "INC-002",
            "Stop Redis",
            "Stops the Redis container in this Compose project.",
            "redis",
            docker_control,
        ),
    ]
)
