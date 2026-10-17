import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fault_injection.registry import FaultRegistry
from fault_injection.router import create_fault_router
from sqlalchemy.exc import SQLAlchemyError

from user_service.config import get_cors_allowed_origins
from user_service.database import check_database, get_pool_stats, log_database_error
from user_service.observability import (
    configure_logging,
    install_http_observability,
    metrics,
)
from user_service.schemas import User, UserCreate
from user_service.service import (
    DatabaseOperationError,
    EmailAlreadyExistsError,
    UserNotFoundError,
    UserService,
    get_user_service,
)

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Service started", extra={"event": "service_started"})
    yield


app = FastAPI(title="User Service", lifespan=lifespan)
app.state.fault_registry = FaultRegistry({"request_delay"})
app.include_router(create_fault_router())
install_http_observability(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allowed_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def get_database_health_checker() -> Callable[[], None]:
    return check_database


@app.get("/health/db")
def database_health(
    response: Response,
    checker: Annotated[Callable[[], None], Depends(get_database_health_checker)],
) -> dict[str, str]:
    try:
        checker()
    except (SQLAlchemyError, OSError, RuntimeError) as error:
        if isinstance(error, SQLAlchemyError):
            log_database_error(error, "health_check")
        else:
            logger.error(
                "Database health check failed",
                extra={
                    "event": "db_connection_failed",
                    "operation": "health_check",
                    "exception_type": type(error).__name__,
                },
            )
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ok"}


def get_database_pool_stats() -> dict[str, int]:
    return get_pool_stats()


@app.get("/internal/stats")
def internal_stats(
    pool_stats: Annotated[dict[str, int], Depends(get_database_pool_stats)],
) -> dict[str, object]:
    return {**metrics.snapshot(), "database_pool": pool_stats}


@app.post("/users", response_model=User, status_code=status.HTTP_201_CREATED)
def create_user(
    user_data: UserCreate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    try:
        return service.create_user(user_data)
    except EmailAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        ) from error
    except DatabaseOperationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database operation failed",
        ) from error


@app.get("/users/{user_id}", response_model=User)
def get_user(
    user_id: int,
    request: Request,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    fault = request.app.state.fault_registry.get_fault_by_type("request_delay")
    if fault is not None:
        delay_ms = fault.parameters["delay_ms"]
        logger.warning(
            "User lookup delayed by runtime fault",
            extra={"event": "fault_injection_applied", "operation": "get_user"},
        )
        time.sleep(delay_ms / 1000)
    try:
        return service.get_user(user_id)
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from error
    except DatabaseOperationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database operation failed",
        ) from error
