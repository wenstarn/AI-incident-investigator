"""Private, allowlisted Docker operations. Only this process owns the socket."""

import os
import secrets
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from incident_controller.docker_control import (
    ALLOWED_SERVICES,
    READABLE_SERVICES,
    DockerControl,
    DockerControlError,
    get_compose_project,
)

app = FastAPI(title="Docker Executor", docs_url=None, redoc_url=None, openapi_url=None)
control = DockerControl(get_compose_project())


class Target(BaseModel):
    service: str
    expected_id: str | None = None


def authorize(x_executor_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("DOCKER_EXECUTOR_TOKEN", "")
    if not expected:
        raise HTTPException(503, "Executor credential unavailable")
    if not x_executor_token or not secrets.compare_digest(x_executor_token, expected):
        raise HTTPException(403, "Forbidden")


def authorize_read(x_executor_read_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("DOCKER_EXECUTOR_READ_TOKEN", "")
    privileged = os.getenv("DOCKER_EXECUTOR_TOKEN", "")
    if not expected or (privileged and secrets.compare_digest(expected, privileged)):
        raise HTTPException(503, "Read-only credential unavailable")
    if not x_executor_read_token or not secrets.compare_digest(
        x_executor_read_token, expected
    ):
        raise HTTPException(403, "Forbidden")


@app.get("/read-only/services/{service}/status", dependencies=[Depends(authorize_read)])
def read_status(service: str) -> dict:
    if service not in READABLE_SERVICES:
        raise HTTPException(404, "Unknown service")
    try:
        return control.inspect_readonly(service)
    except DockerControlError as error:
        raise HTTPException(
            404 if "matching container" in str(error) else 503, "Container unavailable"
        ) from error


@app.get("/read-only/services/{service}/logs", dependencies=[Depends(authorize_read)])
def read_logs(
    service: str,
    tail: int = Query(default=50, ge=1, le=200),
    since_seconds: int | None = Query(default=None, ge=1, le=86400),
) -> dict:
    if service not in READABLE_SERVICES:
        raise HTTPException(404, "Unknown service")
    try:
        return control.logs_readonly(service, tail=tail, since_seconds=since_seconds)
    except DockerControlError as error:
        raise HTTPException(
            404 if "matching container" in str(error) else 503, "Container unavailable"
        ) from error


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/operations/{operation}", dependencies=[Depends(authorize)])
def operate(operation: str, target: Target) -> dict[str, str]:
    if operation not in {"status", "stop", "start", "health", "wait-healthy"}:
        raise HTTPException(404, "Unknown operation")
    if target.service not in ALLOWED_SERVICES:
        raise HTTPException(404, "Unknown service")
    if operation in {"start", "health", "wait-healthy"} and not target.expected_id:
        raise HTTPException(400, "Expected container ID required")
    try:
        if operation == "stop":
            return {"container_id": control.stop(target.service)}
        if operation == "start":
            control.start(target.service, expected_id=target.expected_id)
            return {"status": "started"}
        if operation == "status":
            return {
                "status": control.status(target.service, expected_id=target.expected_id)
            }
        if operation == "health":
            return {
                "status": control.health_status(
                    target.service, expected_id=target.expected_id
                )
            }
        deadline = time.monotonic() + float(
            os.getenv("INCIDENT_RECOVERY_TIMEOUT_SECONDS", "60")
        )
        while time.monotonic() < deadline:
            if (
                control.health_status(target.service, expected_id=target.expected_id)
                == "healthy"
            ):
                return {"status": "healthy"}
            time.sleep(1)
        raise HTTPException(504, "Healthcheck timed out")
    except DockerControlError as error:
        raise HTTPException(503, "Docker operation failed") from error
