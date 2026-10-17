import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from fault_injection.models import Fault, FaultCreate
from fault_injection.registry import (
    FaultConflictError,
    FaultRegistry,
    FaultValidationError,
)


def _authorize(x_fault_token: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("FAULT_INJECTION_TOKEN")
    diagnostics = os.getenv("DIAGNOSTICS_TOKEN")
    if diagnostics and expected and secrets.compare_digest(diagnostics, expected):
        raise HTTPException(
            status_code=503, detail="Fault management credential unavailable"
        )
    if (
        not expected
        or not x_fault_token
        or not secrets.compare_digest(x_fault_token, expected)
    ):
        raise HTTPException(status_code=403, detail="Fault management unavailable")


def _registry(request: Request) -> FaultRegistry:
    return request.app.state.fault_registry


def create_fault_router() -> APIRouter:
    router = APIRouter(
        prefix="/internal/faults",
        include_in_schema=False,
        dependencies=[Depends(_authorize)],
    )

    @router.post("", response_model=Fault, status_code=status.HTTP_201_CREATED)
    def activate(
        data: FaultCreate, registry: Annotated[FaultRegistry, Depends(_registry)]
    ) -> Fault:
        try:
            return registry.set_fault(data)
        except FaultValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except FaultConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("", response_model=list[Fault])
    def list_faults(
        registry: Annotated[FaultRegistry, Depends(_registry)],
    ) -> list[Fault]:
        return registry.get_active_faults()

    @router.get("/{fault_id}", response_model=Fault)
    def get_fault(
        fault_id: str, registry: Annotated[FaultRegistry, Depends(_registry)]
    ) -> Fault:
        fault = registry.get_fault(fault_id)
        if fault is None:
            raise HTTPException(status_code=404, detail="Fault not found")
        return fault

    @router.delete("/{fault_id}", status_code=204)
    def remove_fault(
        fault_id: str, registry: Annotated[FaultRegistry, Depends(_registry)]
    ) -> Response:
        registry.remove_fault(fault_id)
        return Response(status_code=204)

    @router.delete("", status_code=204)
    def clear_faults(
        registry: Annotated[FaultRegistry, Depends(_registry)],
    ) -> Response:
        registry.clear_all()
        return Response(status_code=204)

    return router
