"""HTTP API for local test incident control."""

import logging
import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from incident_controller.fault_client import (
    FaultClientConfigurationError,
    FaultClientError,
    FaultClientForbiddenError,
)
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


def authorize(x_incident_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("INCIDENT_CONTROLLER_TOKEN", "")
    if not expected:
        raise HTTPException(503, "Incident Controller credential unavailable")
    peer_tokens = (
        os.getenv("FAULT_INJECTION_TOKEN", ""),
        os.getenv("DOCKER_EXECUTOR_TOKEN", ""),
    )
    if any(token and secrets.compare_digest(expected, token) for token in peer_tokens):
        raise HTTPException(503, "Incident Controller credentials must be distinct")
    if all(peer_tokens) and secrets.compare_digest(*peer_tokens):
        raise HTTPException(503, "Service credentials must be distinct")
    if not x_incident_token or not secrets.compare_digest(x_incident_token, expected):
        raise HTTPException(403, "Forbidden")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/incidents", response_model=list[IncidentInfo], dependencies=[Depends(authorize)]
)
def list_incidents(
    service: Annotated[IncidentController, Depends(get_controller)],
) -> list[IncidentInfo]:
    return [incident_info(item) for item in service.list_incidents()]


@app.get(
    "/incidents/active",
    response_model=ActiveIncident,
    dependencies=[Depends(authorize)],
)
def active_incident(
    service: Annotated[IncidentController, Depends(get_controller)],
) -> ActiveIncident:
    scenario = service.active_incident()
    return ActiveIncident(incident=incident_info(scenario) if scenario else None)


@app.post(
    "/incidents/{incident_id}/activate",
    response_model=ActiveIncident,
    dependencies=[Depends(authorize)],
)
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
        cause = error.__cause__
        if isinstance(cause, FaultClientConfigurationError):
            detail = "FAULT_INJECTION_TOKEN is not configured in Incident Controller"
        elif isinstance(cause, FaultClientForbiddenError):
            detail = "Fault injection token was rejected by the service"
        elif isinstance(cause, FaultClientError):
            detail = "Fault injection service unavailable"
        else:
            detail = "Docker operation failed"
        logger.exception(
            "Incident dependency failed", extra={"incident_id": incident_id}
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail) from error
    except IncidentOperationError as error:
        logger.exception(
            "Incident activation failed", extra={"incident_id": incident_id}
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Activation failed"
        ) from error
    return ActiveIncident(incident=incident_info(scenario))


@app.post(
    "/incidents/active/reset",
    response_model=ActiveIncident,
    dependencies=[Depends(authorize)],
)
def reset_incident(
    service: Annotated[IncidentController, Depends(get_controller)],
) -> ActiveIncident:
    try:
        service.reset()
    except IncidentDependencyError as error:
        logger.exception("Incident reset dependency failed")
        detail = (
            "Fault injection service unavailable"
            if isinstance(error.__cause__, FaultClientError)
            else "Docker operation failed"
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail) from error
    except IncidentOperationError as error:
        logger.exception("Incident reset failed")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Reset failed"
        ) from error
    return ActiveIncident(incident=None)
