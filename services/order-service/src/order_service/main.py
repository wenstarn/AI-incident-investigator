import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fault_injection.registry import FaultRegistry
from fault_injection.router import create_fault_router
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import SQLAlchemyError

from order_service.config import get_cors_allowed_origins
from order_service.database import check_database, get_pool_stats, log_database_error
from order_service.diagnostics import router as diagnostics_router
from order_service.observability import (
    configure_logging,
    install_http_observability,
    metrics,
)
from order_service.redis_client import check_redis, close_redis_client
from order_service.schemas import Order, OrderCreate, Product, SortOrder
from order_service.service import (
    DatabaseOperationError,
    InsufficientStockError,
    OrderNotFoundError,
    OrderService,
    ProductNotFoundError,
    get_order_service,
)
from order_service.user_client import (
    UnexpectedUserServiceResponseError,
    UserNotFoundError,
    UserServiceTimeoutError,
    UserServiceUnavailableError,
)

logger = logging.getLogger(__name__)
configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Service started", extra={"event": "service_started"})
    yield
    close_redis_client()


app = FastAPI(title="Order Service", lifespan=lifespan)
app.state.fault_registry = FaultRegistry(
    {"request_delay", "user_service_url_override", "redis_url_override"}
)
app.include_router(create_fault_router())
app.include_router(diagnostics_router)
install_http_observability(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allowed_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "order-service"}


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


def get_redis_health_checker() -> Callable[[], None]:
    return check_redis


@app.get("/health/redis")
def redis_health(
    response: Response,
    checker: Annotated[Callable[[], None], Depends(get_redis_health_checker)],
) -> dict[str, str]:
    try:
        checker()
    except (RedisConnectionError, RedisTimeoutError):
        logger.exception(
            "Redis health check failed",
            extra={"event": "redis_connection_failed", "operation": "ping"},
        )
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ok"}


@app.get("/products", response_model=list[Product])
def list_products(
    service: Annotated[OrderService, Depends(get_order_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Product]:
    try:
        return service.list_products(limit, offset)
    except DatabaseOperationError as error:
        raise _database_http_error(error) from error


@app.get("/products/{product_id}", response_model=Product)
def get_product(
    product_id: int,
    service: Annotated[OrderService, Depends(get_order_service)],
) -> Product:
    try:
        return service.get_product(product_id)
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="Product not found") from error
    except DatabaseOperationError as error:
        raise _database_http_error(error) from error


@app.post("/orders", response_model=Order, status_code=status.HTTP_201_CREATED)
def create_order(
    order_data: OrderCreate,
    service: Annotated[OrderService, Depends(get_order_service)],
) -> Order:
    try:
        return service.create_order(order_data)
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="Product not found") from error
    except InsufficientStockError as error:
        raise HTTPException(status_code=409, detail="Insufficient stock") from error
    except UserNotFoundError as error:
        raise HTTPException(status_code=404, detail="User not found") from error
    except UserServiceTimeoutError as error:
        raise HTTPException(status_code=504, detail="User Service timed out") from error
    except UserServiceUnavailableError as error:
        raise HTTPException(
            status_code=502, detail="User Service unavailable"
        ) from error
    except UnexpectedUserServiceResponseError as error:
        raise HTTPException(
            status_code=502, detail="Unexpected response from User Service"
        ) from error
    except DatabaseOperationError as error:
        raise _database_http_error(error) from error


@app.get("/orders/{order_id}", response_model=Order)
def get_order(
    order_id: int,
    service: Annotated[OrderService, Depends(get_order_service)],
) -> Order:
    try:
        return service.get_order(order_id)
    except OrderNotFoundError as error:
        raise HTTPException(status_code=404, detail="Order not found") from error
    except DatabaseOperationError as error:
        raise _database_http_error(error) from error


@app.get("/orders", response_model=list[Order])
def list_orders(
    service: Annotated[OrderService, Depends(get_order_service)],
    user_id: Annotated[int | None, Query(gt=0)] = None,
    sort_order: SortOrder = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[Order]:
    try:
        return service.list_orders(user_id, sort_order, limit)
    except DatabaseOperationError as error:
        raise _database_http_error(error) from error


def _database_http_error(error: DatabaseOperationError) -> HTTPException:
    return HTTPException(status_code=503, detail="Database operation failed")
