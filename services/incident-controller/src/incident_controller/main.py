"""HTTP API for local test incident control."""

import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from incident_controller.observability import install_observability
from incident_controller.scenarios import IncidentScenario
from incident_controller.service import (
    IncidentAlreadyActiveError,
    IncidentController,
    IncidentDependencyError,
    IncidentNotFoundError,
    IncidentOperationError,
    controller,
)

logger = logging.getLogger(__name__)
app = FastAPI(title="Incident Controller")
install_observability(app)


class IncidentInfo(BaseModel):
    id: str
    name: str
    description: str


class ActiveIncident(BaseModel):
    incident: IncidentInfo | None


def get_controller() -> IncidentController:
    return controller


def incident_info(scenario: IncidentScenario) -> IncidentInfo:
    return IncidentInfo(
        id=scenario.id, name=scenario.name, description=scenario.description
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/incidents", response_model=list[IncidentInfo])
def list_incidents(
    service: Annotated[IncidentController, Depends(get_controller)],
) -> list[IncidentInfo]:
    return [incident_info(item) for item in service.list_incidents()]


@app.get("/incidents/active", response_model=ActiveIncident)
def active_incident(
    service: Annotated[IncidentController, Depends(get_controller)],
) -> ActiveIncident:
    scenario = service.active_incident()
    return ActiveIncident(incident=incident_info(scenario) if scenario else None)


@app.post("/incidents/{incident_id}/activate", response_model=ActiveIncident)
def activate_incident(
    incident_id: str,
    service: Annotated[IncidentController, Depends(get_controller)],
) -> ActiveIncident:
    try:
        scenario = service.activate(incident_id)
    except IncidentNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found") from error
    except IncidentAlreadyActiveError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An incident is already active"
        ) from error
    except IncidentDependencyError as error:
        logger.exception("Docker operation failed", extra={"incident_id": incident_id})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Docker operation failed"
        ) from error
    except IncidentOperationError as error:
        logger.exception(
            "Incident activation failed", extra={"incident_id": incident_id}
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Activation failed"
        ) from error
    return ActiveIncident(incident=incident_info(scenario))


@app.post("/incidents/active/reset", response_model=ActiveIncident)
def reset_incident(
    service: Annotated[IncidentController, Depends(get_controller)],
) -> ActiveIncident:
    try:
        service.reset()
    except IncidentDependencyError as error:
        logger.exception("Docker reset failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Docker operation failed"
        ) from error
    except IncidentOperationError as error:
        logger.exception("Incident reset failed")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Reset failed"
        ) from error
    return ActiveIncident(incident=None)
